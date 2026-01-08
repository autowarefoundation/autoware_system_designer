import * as path from 'path';
import { ExtensionContext, workspace } from 'vscode';
import {
    LanguageClient,
    LanguageClientOptions,
    ServerOptions,
    TransportKind,
    Executable
} from 'vscode-languageclient/node';

let client: LanguageClient;

export function activate(context: ExtensionContext) {
    // Get configuration
    const config = workspace.getConfiguration('autowareSystemDesigner.languageServer');

    // The server is implemented in Python
    const serverModule = path.join(__dirname, '..', 'server', 'server.py');

    // Use Python executable from configuration or default to 'python'
    const pythonPath = config.get<string>('path', 'python');

    // If the extension is launched in debug mode then the debug server options are used
    // Otherwise the run options are used
    const serverOptions: ServerOptions = {
        command: pythonPath,
        args: [serverModule],
        options: {
            cwd: context.extensionPath
        }
    };

    // Options to control the language client
    const clientOptions: LanguageClientOptions = {
        // Register the server for YAML files with specific extensions
        documentSelector: [
            { scheme: 'file', language: 'yaml', pattern: '**/*.{node,module,system,parameter_set}.yaml' }
        ],
        synchronize: {
            // Notify the server about file changes to YAML files
            fileEvents: workspace.createFileSystemWatcher('**/*.{node,module,system,parameter_set}.yaml')
        },
        outputChannelName: 'Autoware System Designer Language Server'
    };

    // Create the language client and start the client.
    client = new LanguageClient(
        'autowareSystemDesigner',
        'Autoware System Designer Language Server',
        serverOptions,
        clientOptions
    );

    // Start the client. This will also launch the server
    client.start();
}

export function deactivate(): Thenable<void> | undefined {
    if (!client) {
        return undefined;
    }
    return client.stop();
}
