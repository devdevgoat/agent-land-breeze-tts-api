import ArgumentParser
import Foundation

struct Service: ParsableCommand {
    static let configuration = CommandConfiguration(
        abstract: "Install or remove a launchd agent for auto-start on login.",
        subcommands: [Install.self, Remove.self]
    )

    private static let serviceLabel = "com.breeze-tts.mlx"

    static var plistDir: URL {
        FileManager.default.homeDirectoryForCurrentUser.appending(path: "Library/LaunchAgents")
    }

    static var plistPath: URL {
        plistDir.appending(path: "\(serviceLabel).plist")
    }

    struct Install: ParsableCommand {
        static let configuration = CommandConfiguration(
            abstract: "Install the launchd agent and start on login."
        )

        func run() throws {
            let cfg = Env.settings()
            let logsDir = Repo.root.appending(path: "logs")
            try? FileManager.default.createDirectory(at: logsDir, withIntermediateDirectories: true)

            let venvPython = Repo.root.appending(path: ".venv/bin/python").path(percentEncoded: false)
            guard FileManager.default.isExecutableFile(atPath: venvPython) else {
                throw ValidationError("Venv not found at .venv/. Run: breeze launch --setup-only")
            }

            let envKeys = [
                "BREEZE_PORT", "BREEZE_BIND", "BREEZE_VOICES_DIR", "BREEZE_RESPONSES_DIR",
                "BREEZE_MODELS_DIR", "BREEZE_MLX_MODEL_DIR", "BREEZE_MLX_REPO",
                "BREEZE_MLX_COMPILE", "BREEZE_MLX_FAST_FIRST", "BREEZE_MLX_CACHE_GB",
                "BREEZE_SYNC_WAIT_SECONDS", "BREEZE_MAX_PENDING", "BREEZE_RESPONSE_TTL_SECONDS",
                "BREEZE_MIN_LEAD_SECONDS", "BREEZE_MAX_NEW_TOKENS",
            ]
            var envDict: [String: String] = [:]
            for k in envKeys { if let v = cfg[k] { envDict[k] = v } }

            let plist: [String: Any] = [
                "Label": Service.serviceLabel,
                "ProgramArguments": [
                    venvPython,
                    Repo.root.appending(path: "runtimes/mlx/mlx_api.py").path(percentEncoded: false),
                ],
                "WorkingDirectory": Repo.root.path(percentEncoded: false),
                "EnvironmentVariables": envDict,
                "RunAtLoad": true,
                "KeepAlive": ["SuccessfulExit": false],
                "StandardOutPath": logsDir.appending(path: "launchd-stdout.log")
                    .path(percentEncoded: false),
                "StandardErrorPath": logsDir.appending(path: "launchd-stderr.log")
                    .path(percentEncoded: false),
                "ProcessType": "Interactive",
            ]

            try? FileManager.default.createDirectory(
                at: Service.plistDir, withIntermediateDirectories: true)

            let uid = getuid()
            Shell.exec(
                ["/usr/bin/env", "launchctl", "bootout", "gui/\(uid)",
                 Service.plistPath.path(percentEncoded: false)],
                passthrough: false
            )

            let data = try PropertyListSerialization.data(
                fromPropertyList: plist, format: .xml, options: 0)
            try data.write(to: Service.plistPath)

            let status = Shell.exec([
                "/usr/bin/env", "launchctl", "bootstrap", "gui/\(uid)",
                Service.plistPath.path(percentEncoded: false),
            ])
            guard status == 0 else {
                throw ValidationError("launchctl bootstrap failed (exit \(status))")
            }

            print("Installed and loaded \(Service.plistPath.path(percentEncoded: false))")
            print("Logs: \(logsDir.path(percentEncoded: false))/launchd-{stdout,stderr}.log")
            print("Remove: breeze service remove")
        }
    }

    struct Remove: ParsableCommand {
        static let configuration = CommandConfiguration(
            abstract: "Unload and remove the launchd agent."
        )

        func run() throws {
            let uid = getuid()
            Shell.exec(
                ["/usr/bin/env", "launchctl", "bootout", "gui/\(uid)",
                 Service.plistPath.path(percentEncoded: false)],
                passthrough: false
            )
            if FileManager.default.fileExists(atPath: Service.plistPath.path(percentEncoded: false)) {
                try? FileManager.default.removeItem(at: Service.plistPath)
                print("Removed \(Service.plistPath.path(percentEncoded: false))")
            } else {
                print("Plist not found; nothing to remove.")
            }
        }
    }
}
