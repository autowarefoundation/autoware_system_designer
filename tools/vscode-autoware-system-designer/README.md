# Autoware System Designer VSCode Extension

A VSCode extension that provides language server support for Autoware System Design Format YAML files, offering validation, auto-completion, and interactive features.

## Features

The language server resolves designs with the `autoware_system_designer` package itself, so
connection rules, schemas and format versions follow whatever version of the package is bundled
with the extension (or checked out next to it during development).

### Connection Validation

- **Port resolution** against the same `instance.port` key space the designer builds at deploy time
- **Wildcard connections** — `*`, `^` and `+` are expanded and reported when nothing matches
- **Port role pairing** — publisher/subscriber and server/client, checked by the designer's own parser
- **External interfaces** — connections must reference declared module inputs and outputs
- **Message type compatibility** between the two ends of a connection (warning)
- **Cross-file validation** through the workspace entity registry

### Schema Diagnostics

- **Schema violations** reported at the YAML path that caused them
- **Format version** incompatibilities reported against the supported version
- **File and design name** mismatches

### Auto-completion

- **Port roles** after `instance.` — the roles the instance actually exposes
- **Port names** after `instance.role.`, with their message types

### Go-to-Definition

- Jump from an entity name to its definition file
- Jump from a connection reference to the instance's entity

### Hover Documentation

- **Entity information** — type, file, launch configuration, ports, instances and components

## Supported File Types

- `*.node.yaml` - Node entity definitions
- `*.module.yaml` - Module entity definitions
- `*.system.yaml` - System entity definitions
- `*.parameter_set.yaml` - Parameter set definitions

## Installation

### Prerequisites

| Tool                                                        | Version | Purpose                           |
| ----------------------------------------------------------- | ------- | --------------------------------- |
| [Node.js](https://nodejs.org/)                              | 18+     | Build toolchain                   |
| [pnpm](https://pnpm.io/)                                    | 8+      | Package manager                   |
| [TypeScript](https://www.typescriptlang.org/)               | 4.9+    | Compile extension source          |
| [@vscode/vsce](https://github.com/microsoft/vscode-vsce)    | latest  | Package `.vsix` (production only) |
| Python                                                      | 3.8+    | Language server runtime           |
| pip packages: `pygls>=1.0.0,<2.0.0`, `lsprotocol>=2022.0.0` | —       | Language server libraries         |

### 1. Install Node.js and pnpm

```bash
# Node.js (via nvm — recommended)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
nvm install --lts
nvm use --lts

# pnpm
npm install -g pnpm
```

### 2. Install TypeScript and vsce globally

```bash
pnpm add -g typescript @vscode/vsce
```

### 3. Install Node.js dependencies

```bash
cd path-to/vscode-autoware-system-designer/
pnpm install
```

### 4. Install Python language server dependencies

```bash
pip install -r server/requirements.txt
```

### Build and Install (production)

```bash
vsce package --no-dependencies
code --install-extension vscode-autoware-system-designer-*.vsix
```

### Development (no packaging needed)

1. Open this directory in VSCode:

   ```bash
   code path-to/vscode-autoware-system-designer/
   ```

2. Press **F5** — VSCode compiles the TypeScript and opens an Extension Development Host with the extension loaded live.
3. Edit `src/extension.ts` and the TypeScript compiler (`tsc --watch`) recompiles automatically; reload the host window (`Ctrl+Shift+P` → "Reload Window") to pick up changes.
4. Logs appear in the host window under **Output → "Autoware System Designer Language Server"**.

Enable verbose Python server logging via workspace settings in the host window:

```json
{
  "autowareSystemDesigner.languageServer.debug": true
}
```

## Configuration

### Language Server Path

Set the Python executable path used for the language server:

```json
{
  "autowareSystemDesigner.languageServer.path": "/usr/bin/python3"
}
```

### Debug Logging

Enable debug logging for troubleshooting:

```json
{
  "autowareSystemDesigner.languageServer.debug": true
}
```

## Architecture

### Language Server (Python)

The language server (`server/server.py`) implements the Language Server Protocol using `pygls`:

- **Entity Registry** - Maintains a registry of all parsed entities
- **Connection Validation** - Validates connection references and types
- **Completion Provider** - Provides context-aware auto-completion
- **Definition Provider** - Implements go-to-definition functionality
- **Hover Provider** - Shows detailed documentation on hover

### VSCode Extension (TypeScript)

The VSCode client (`src/extension.ts`) registers the language server and handles:

- **Language registration** for YAML files with specific extensions
- **Server lifecycle** management
- **Configuration** handling

## Development

### Project Structure

```text
vscode-autoware-system-designer/
├── src/                         # TypeScript client
│   └── extension.ts             # Extension entry point
├── server/                      # Python language server
│   ├── server.py                # Entry point
│   ├── base_server.py           # LSP handlers and wiring
│   ├── document_processor.py    # Parse + publish diagnostics
│   ├── registry_manager.py      # Workspace entity registry
│   ├── validation_engine.py     # Connection and naming diagnostics
│   ├── resolution_service.py    # Port and message type resolution
│   ├── providers/               # Completion, definition, hover, signature help
│   ├── test/                    # pytest suite for the server
│   ├── bundled/                 # autoware_system_designer, copied in at package time
│   └── requirements.txt         # Python dependencies
├── scripts/bundle_python_pkg.js # Copies the designer package into server/bundled/
├── package.json                 # Extension manifest
├── tsconfig.json                # TypeScript configuration
└── language-configuration.json  # YAML language configuration
```

### Testing

```bash
# Language server tests (uses the designer package next to the extension)
python3 -m pytest server/test

# Lint the TypeScript client
pnpm lint
```

### Debugging

1. Set breakpoints in the language server code
2. Use VSCode's debugger with the Extension Development Host
3. Check the language server output channel for logs

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly with example files
5. Submit a pull request

## License

Licensed under the Apache License, Version 2.0.
