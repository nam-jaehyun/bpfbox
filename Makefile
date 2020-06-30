.PHONY: all
all: package

.PHONY: install
install: package libebph
	echo "Not yet implemented"

.PHONY: package
package: libebph
	pip3 install -e . -r requirements.txt

.PHONY: libbpfbox
libbpfbox:
	$(MAKE) -C bpfbox/libbpfbox

.PHONY: test
test:
	$(MAKE) -C tests
