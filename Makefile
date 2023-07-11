install:
	pip3 install -e .[dev]

format:
	black .

mypy:
	mypy --disallow-untyped-defs src/codetell

build:
	rm -rf dist/
	python3 -m build

publish:
	python3 -m twine upload dist/*

.PHONY: install
