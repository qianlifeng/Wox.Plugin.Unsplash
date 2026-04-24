.PHONY: help init check-init check-dev-deps install clean lint format build test package

DIST_DIR := dist
SRC_DIR := src

ifeq ($(OS),Windows_NT)
SHELL := powershell.exe
.SHELLFLAGS := -NoProfile -ExecutionPolicy Bypass -Command
PYTHON ?= python
else
PYTHON ?= python3
endif

help:
	@echo "Available commands:"
	@echo "  make init    - Initialize template values interactively"
	@echo "  make install  - Install project dependencies"
	@echo "  make clean   - Clean build directory"
	@echo "  make build   - Build project"
	@echo "  make package - Build plugin package"

init:
	$(PYTHON) scripts/init-wox-project.py

check-init:
	@$(PYTHON) scripts/init-wox-project.py --check-initialized

check-dev-deps:
	@uv run python -c "import importlib.util, sys; missing = [name for name in ('ruff', 'mypy') if importlib.util.find_spec(name) is None]; missing and (print('Development dependencies are not installed. Run make install first.', file=sys.stderr), print('Missing packages: ' + ', '.join(missing), file=sys.stderr), sys.exit(1))"

install: check-init
	uv sync --all-extras

lint: check-init check-dev-deps
	uv run ruff check src
	uv run mypy src

format: check-init check-dev-deps
	uv run ruff format src

test: check-init
	uv run python -m unittest tests/test_friendly_names.py

clean:
ifeq ($(OS),Windows_NT)
	if (Test-Path '$(DIST_DIR)') { Remove-Item -Recurse -Force '$(DIST_DIR)' }
else
	rm -rf $(DIST_DIR)
endif

build: lint format
ifeq ($(OS),Windows_NT)
	if (Test-Path '$(DIST_DIR)') { Remove-Item -Recurse -Force '$(DIST_DIR)' }
	New-Item -ItemType Directory -Force -Path '$(DIST_DIR)\dependencies' | Out-Null
	uv pip freeze | Out-File -FilePath 'requirements.txt' -Encoding ascii
	uv pip install -r requirements.txt --target "$(DIST_DIR)\dependencies"
	Remove-Item -Force 'requirements.txt'
	Copy-Item '$(SRC_DIR)\*' -Destination '$(DIST_DIR)' -Recurse -Force
	Get-ChildItem -Path '$(DIST_DIR)\dependencies' -Directory -Recurse | Where-Object { $$_.Name -like '*.dist-info' -or $$_.Name -like '*.egg-info' } | Remove-Item -Recurse -Force
	Get-ChildItem -Path '$(DIST_DIR)\dependencies' -File -Recurse | Where-Object { $$_.Name -like '__editable__*' -or $$_.Name -eq '.lock' } | Remove-Item -Force
	Get-ChildItem -Path '$(DIST_DIR)\dependencies' -Directory -Recurse | Sort-Object FullName -Descending | Where-Object { $$_.Name -like '*mypy*' -or $$_.Name -eq 'ruff' -or $$_.Name -eq 'bin' } | Remove-Item -Recurse -Force
	Set-Content -Path '$(DIST_DIR)\__init__.py' -Encoding ascii -Value @('import os','import sys','','# Add dependencies directory to Python path','deps_dir = os.path.join(os.path.dirname(__file__), "dependencies")','if deps_dir not in sys.path:','    sys.path.insert(0, deps_dir)','','from .main import plugin','','__all__ = ["plugin"]')
	Copy-Item 'plugin.json' -Destination '$(DIST_DIR)\plugin.json' -Force
	New-Item -ItemType Directory -Force -Path '$(DIST_DIR)\images' | Out-Null
	Copy-Item 'images\*' -Destination '$(DIST_DIR)\images' -Recurse -Force
else
	rm -rf $(DIST_DIR)
	mkdir -p $(DIST_DIR)/dependencies
	uv pip freeze > requirements.txt
	uv pip install -r requirements.txt --target $(DIST_DIR)/dependencies
	rm requirements.txt
	cp -r $(SRC_DIR)/* $(DIST_DIR)/
	find $(DIST_DIR)/dependencies \( -type d -name "*.dist-info" -o -type d -name "*.egg-info" \) -exec rm -rf {} +
	find $(DIST_DIR)/dependencies \( -type f -name "__editable__*" -o -type f -name ".lock" \) -exec rm -f {} +
	rm -rf $(DIST_DIR)/dependencies/*mypy* $(DIST_DIR)/dependencies/ruff $(DIST_DIR)/dependencies/bin
	printf '%s\n' 'import os' 'import sys' '' '# Add dependencies directory to Python path' 'deps_dir = os.path.join(os.path.dirname(__file__), "dependencies")' 'if deps_dir not in sys.path:' '    sys.path.insert(0, deps_dir)' '' 'from .main import plugin' '' '__all__ = ["plugin"]' > $(DIST_DIR)/__init__.py
	cp plugin.json $(DIST_DIR)/plugin.json
	mkdir -p $(DIST_DIR)/images
	cp images/* $(DIST_DIR)/images/
endif

package: check-init build
ifeq ($(OS),Windows_NT)
	if (Test-Path 'wox.plugin.{{.Name}}.zip') { Remove-Item -Force 'wox.plugin.{{.Name}}.zip' }
	if (Test-Path 'wox.plugin.{{.Name}}.wox') { Remove-Item -Force 'wox.plugin.{{.Name}}.wox' }
	Compress-Archive -Path '$(DIST_DIR)\*' -DestinationPath 'wox.plugin.{{.Name}}.zip'
	Move-Item 'wox.plugin.{{.Name}}.zip' 'wox.plugin.{{.Name}}.wox'
	if (Test-Path '$(DIST_DIR)') { Remove-Item -Recurse -Force '$(DIST_DIR)' }
else
	cd $(DIST_DIR) && zip -r "../wox.plugin.{{.Name}}.wox" .
	rm -rf $(DIST_DIR)
endif
