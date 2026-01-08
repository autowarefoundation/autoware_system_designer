"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.deactivate = exports.activate = void 0;
const path = require("path");
const vscode_1 = require("vscode");
const node_1 = require("vscode-languageclient/node");
let client;
function activate(context) {
    // Get configuration
    const config = vscode_1.workspace.getConfiguration('autowareSystemDesigner.languageServer');
    // The server is implemented in Python
    const serverModule = path.join(__dirname, '..', 'server', 'server.py');
    // Use Python executable from configuration or default to 'python'
    const pythonPath = config.get('path', 'python');
    // If the extension is launched in debug mode then the debug server options are used
    // Otherwise the run options are used
    const serverOptions = {
        command: pythonPath,
        args: [serverModule],
        options: {
            cwd: context.extensionPath
        }
    };
    // Options to control the language client
    const clientOptions = {
        // Register the server for YAML files with specific extensions
        documentSelector: [
            { scheme: 'file', language: 'yaml', pattern: '**/*.{node,module,system,parameter_set}.yaml' }
        ],
        synchronize: {
            // Notify the server about file changes to YAML files
            fileEvents: vscode_1.workspace.createFileSystemWatcher('**/*.{node,module,system,parameter_set}.yaml')
        },
        outputChannelName: 'Autoware System Designer Language Server'
    };
    // Create the language client and start the client.
    client = new node_1.LanguageClient('autowareSystemDesigner', 'Autoware System Designer Language Server', serverOptions, clientOptions);
    // Start the client. This will also launch the server
    client.start();
}
exports.activate = activate;
function deactivate() {
    if (!client) {
        return undefined;
    }
    return client.stop();
}
exports.deactivate = deactivate;
//# sourceMappingURL=extension.js.map