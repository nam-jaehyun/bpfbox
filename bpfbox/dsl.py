"""
    🐝 BPFBox 📦  Application-transparent sandboxing rules with eBPF.
    Copyright (C) 2020  William Findlay

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.

    Implements the parser for bpfbox's policy DSL.

    2020-Jul-04  William Findlay  Created this.
"""

from typing import Callable

from pyparsing import *

from bpfbox.bpf_program import BPFProgram
from bpfbox.logger import get_logger
from bpfbox.flags import BPFBOX_ACTION, FS_ACCESS, IPC_ACCESS

logger = get_logger()

comma = Literal(',').suppress()
quoted_string = QuotedString('"') | QuotedString("'")
comment = QuotedString(quoteChar='/*', endQuoteChar='*/', multiline=True).suppress()
lparen = Literal('(').suppress()
rparen = Literal(')').suppress()

pathname = quoted_string

fs_access = Word('rwaxligsu')
signal_access = Group(Keyword('kill') | Keyword('chld') | Keyword('stop') | Keyword('misc') | Keyword('check'))


class PolicyGenerator:
    """
    Parses policy files and generates rules for the BPF programs to enforce.
    """

    def __init__(self, bpf_program: BPFProgram):
        self.bpf_program = bpf_program
        self.bnf = self._make_bnf()
        self.exe = None
        self.commands = []

    def process_policy_file(self, policy_file: str):
        with open(policy_file, 'r') as f:
            self.process_policy_text(f.read())

    def process_policy_text(self, policy_text: str):
        self._parse_policy_text(policy_text)

        assert self.exe is not None
        self.bpf_program.add_profile(self.exe, True)

        for command in self.commands:
            command(self.exe)

    def _parse_policy_text(self, policy_text: str) -> Dict:
        try:
            return self.bnf.parseString(policy_text, True).asDict()
        except ParseException as pe:
            logger.error('Unable to parse profile:')
            logger.error("    " + pe.line)
            logger.error("    " + " " * (pe.column - 1) + "^")
            logger.error("    %s" % (pe))
            raise pe

    def _do_rule_common(self, rule):
        rule_actions = [a for a in rule['macros'] if a in ['allow', 'taint', 'audit']]
        if not 'taint' in rule_actions:
            rule_actions.append('allow')
        # fs rule
        if rule['type'] == 'fs':
            pathname = rule['pathname']
            access = FS_ACCESS.from_string(rule['access'])
            action = BPFBOX_ACTION.from_actions(rule_actions)
            self.commands.append(lambda exe: self.bpf_program.add_fs_rule(exe, pathname, access, action))
        # procfs rule
        elif rule['type'] == 'proc':
            pathname = rule['pathname']
            access = FS_ACCESS.from_string(rule['access'])
            action = BPFBOX_ACTION.from_actions(rule_actions)
            self.commands.append(lambda exe: self.bpf_program.add_procfs_rule(exe, pathname, access, action))
        # signal rule
        elif rule['type'] == 'signal':
            pathname = rule['pathname']
            access = IPC_ACCESS.from_string(rule['access'])
            action = BPFBOX_ACTION.from_actions(rule_actions)
            self.commands.append(lambda exe: self.bpf_program.add_ipc_rule(exe, pathname, access, action))
        else:
            raise Exception('Unknown rule type')

    def _rule_action(self, toks):
        rule = toks.asDict()['rules'][0]
        self._do_rule_common(rule)

    def _block_action(self, toks):
        block = toks.asDict()['blocks'][0]
        for rule in block['rules']:
            rule['macros'] += block['macros']
            self._do_rule_common(rule)

    def _profile_macro_action(self, toks):
        self.exe = toks[0]

    def _make_bnf(self) -> ParserElement:
        # Special required macro for profile
        profile_macro = (
            Literal('#![').suppress()
            + Keyword('profile').suppress()
            + quoted_string('profile')
            + Literal(']').suppress()
            + LineEnd().suppress()
        ).setParseAction(self._profile_macro_action)

        # Rules
        rule = self._rule().setParseAction(self._rule_action)

        # Blocks
        block = self._block().setParseAction(self._block_action)

        return ZeroOrMore(comment) + profile_macro + ZeroOrMore(
            (rule('rules*') | block('blocks*') | comment)
        )

    def _self_exe(self, toks):
        return self.exe

    def _macro_contents(self) -> ParserElement:
        taint = Keyword('taint')
        allow = Keyword('allow')
        audit = Keyword('audit')
        return allow | taint | audit

    def _macro(self) -> ParserElement:
        macro_contents = self._macro_contents()
        return (
            Literal('#[').suppress() + macro_contents + Literal(']').suppress()
        )

    def _fs_rule(self) -> ParserElement:
        rule_type = Literal('fs')('type')
        return (
            rule_type
            + lparen
            + pathname('pathname')
            + comma
            + fs_access('access')
            + rparen
        )

    def _procfs_rule(self) -> ParserElement:
        rule_type = Literal('proc')('type')
        return rule_type + lparen + pathname('pathname') + comma + fs_access('access') + rparen

    def _signal_rule(self) -> ParserElement:
        rule_type = Literal('signal')('type')
        pathname_or_self = pathname | Keyword('self').setParseAction(self._self_exe)
        return rule_type + lparen + pathname_or_self('pathname') + comma + signal_access('access') + rparen

    def _rule(self) -> ParserElement:
        fs_rule = self._fs_rule()
        procfs_rule = self._procfs_rule()
        signal_rule = self._signal_rule()
        # TODO add more rule types here
        return Group(Group(ZeroOrMore(self._macro()))('macros') + (fs_rule | procfs_rule | signal_rule))

    def _block(self) -> ParserElement:
        begin = Literal('{').suppress()
        end = Literal('}').suppress()
        return Group(
            Group(ZeroOrMore(self._macro()))('macros')
            + Group(begin + ZeroOrMore(self._rule()) + end)('rules')
        )
