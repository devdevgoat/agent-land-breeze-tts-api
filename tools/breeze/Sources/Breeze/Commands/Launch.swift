import ArgumentParser
import Darwin
import Foundation

struct Launch: ParsableCommand {
    static let configuration = CommandConfiguration(
        abstract: "Set up Python venv, install dependencies, and start the MLX TTS server."
    )

    @Flag(name: .long, help: "Only install dependencies; don't start the server.")
    var setupOnly = false

    func run() throws {
        let cfg = Env.settings()

        guard let python = Self.findPython() else {
            throw ValidationError(
                "Python 3.12+ required but not found. Install with: brew install python@3.12")
        }

        let (pyVer, _) = Shell.run([python, "--version"])
        print("Python:  \(pyVer) (\(python))")

        let host = cfg["BREEZE_BIND"] ?? "0.0.0.0"
        let port = cfg["BREEZE_PORT"] ?? "7860"
        let mlxRepo = cfg["BREEZE_MLX_REPO"] ?? "rishikksh20/Breeze-TTS-2-mlx"
        print("Runtime: MLX (\(mlxRepo))")
        print("Address: \(host):\(port)\n")

        ensureVenv(python: python)
        installRequirements()
        ensureBreezeTTSMLX()

        if setupOnly {
            print("\nSetup complete. Start with: breeze launch")
            return
        }

        let modelPath = Env.modelPath()
        let modelContents = (try? FileManager.default.contentsOfDirectory(
            atPath: modelPath.path(percentEncoded: false))) ?? []
        guard modelContents.contains(where: { $0.hasSuffix(".safetensors") }) else {
            throw ValidationError(
                "Model not found at \(modelPath.path(percentEncoded: false)). Run: breeze download")
        }

        print("\nStarting Breeze TTS MLX server...")
        let displayHost = host == "0.0.0.0" ? "127.0.0.1" : host
        print("Health check: curl http://\(displayHost):\(port)/health\n")

        for (k, v) in cfg { setenv(k, v, 1) }
        FileManager.default.changeCurrentDirectoryPath(Repo.root.path(percentEncoded: false))

        let pyPath = venvPython.path(percentEncoded: false)
        let apiPath = Repo.root.appending(path: "runtimes/mlx/mlx_api.py").path(percentEncoded: false)
        let cArgs = [pyPath, apiPath].map { strdup($0) } + [nil]
        execv(pyPath, cArgs)
        perror("execv")
        Foundation.exit(1)
    }

    // MARK: - Python discovery

    private static func findPython() -> String? {
        let candidates = [
            "/opt/homebrew/bin/python3.12",
            "/opt/homebrew/bin/python3.13",
            "/opt/homebrew/bin/python3.14",
            "/usr/local/bin/python3.12",
            "/usr/local/bin/python3.13",
        ]
        for c in candidates {
            if FileManager.default.isExecutableFile(atPath: c) {
                let (ver, status) = Shell.run([c, "--version"])
                if status == 0 && ver.contains("3.1") { return c }
            }
        }
        let (path, status) = Shell.run(["which", "python3"])
        if status == 0 {
            let (ver, _) = Shell.run([path, "--version"])
            if let range = ver.range(of: #"3\.(\d+)"#, options: .regularExpression) {
                let minor = Int(ver[range].split(separator: ".").last ?? "0") ?? 0
                if minor >= 12 { return path }
            }
        }
        return nil
    }

    // MARK: - Venv management

    private var venvDir: URL { Repo.root.appending(path: ".venv") }
    private var venvPython: URL { venvDir.appending(path: "bin/python") }
    private var venvPip: URL { venvDir.appending(path: "bin/pip") }
    private var stampFile: URL { venvDir.appending(path: ".requirements-stamp") }
    private var requirementsFile: URL { Repo.root.appending(path: "runtimes/mlx/requirements.txt") }

    private func sha256(_ path: URL) -> String {
        let (hash, _) = Shell.run(["shasum", "-a", "256", path.path(percentEncoded: false)])
        return String(hash.split(separator: " ").first ?? "")
    }

    private func ensureVenv(python: String) {
        if !FileManager.default.fileExists(atPath: venvPython.path(percentEncoded: false)) {
            print("Creating Python venv at .venv/ ...")
            let status = Shell.exec([python, "-m", "venv", venvDir.path(percentEncoded: false)])
            guard status == 0 else {
                fputs("Failed to create venv\n", stderr)
                Foundation.exit(1)
            }
        }
    }

    private func installRequirements() {
        let currentHash = sha256(requirementsFile)
        let stampHash = (try? String(contentsOf: stampFile, encoding: .utf8))?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if currentHash == stampHash && !currentHash.isEmpty {
            print("Dependencies up to date (stamp matches)")
            return
        }
        print("Installing Python dependencies...")
        let status = Shell.exec([
            venvPip.path(percentEncoded: false), "install", "-q",
            "-r", requirementsFile.path(percentEncoded: false),
        ])
        guard status == 0 else {
            fputs("pip install failed\n", stderr)
            Foundation.exit(1)
        }
        try? currentHash.write(to: stampFile, atomically: true, encoding: .utf8)
    }

    private func ensureBreezeTTSMLX() {
        let (_, status) = Shell.run([venvPython.path(percentEncoded: false), "-c", "import breeze_tts_mlx"])
        if status == 0 { return }

        print("Installing breeze-tts-mlx from source...")
        let cloneDir = Repo.root.appending(path: "breeze-tts-mlx")
        if !FileManager.default.fileExists(atPath: cloneDir.path(percentEncoded: false)) {
            let s = Shell.exec([
                "/usr/bin/env", "git", "clone",
                "https://github.com/rishikksh20/breeze-tts-mlx.git",
                cloneDir.path(percentEncoded: false),
            ])
            guard s == 0 else {
                fputs("git clone failed\n", stderr)
                Foundation.exit(1)
            }
        }
        let s = Shell.exec([
            venvPip.path(percentEncoded: false), "install", "-e",
            cloneDir.path(percentEncoded: false),
        ])
        guard s == 0 else {
            fputs("pip install breeze-tts-mlx failed\n", stderr)
            Foundation.exit(1)
        }
    }
}
