import ArgumentParser
import Foundation

struct Setup: ParsableCommand {
    static let configuration = CommandConfiguration(
        abstract: "One-command setup: install deps, clone repo, configure, download model, and optionally launch."
    )

    @Flag(name: .long, help: "Start the server after setup.")
    var launch = false

    @Flag(name: .long, help: "Skip installing Homebrew packages.")
    var skipBrew = false

    private static let repoURL = "https://github.com/devdevgoat/agent-land-breeze-tts-api.git"

    private func emit(_ s: String) {
        fputs(s + "\n", stdout)
        fflush(stdout)
    }

    func run() throws {
        let breezeBin = resolveBreezeBinary()

        emit("=== Breeze TTS Setup ===\n")

        // 1. Check ARM64
        let (arch, _) = Shell.run(["uname", "-m"])
        guard arch == "arm64" else {
            throw ValidationError("Apple Silicon required (got \(arch)). Use the NVIDIA/Docker path instead.")
        }
        emit("arch:    arm64")

        // 2. Homebrew packages
        if !skipBrew {
            ensureBrew()
            ensureBrewPackage("python@3.12", binary: "python3.12",
                              searchPaths: ["/opt/homebrew/bin/python3.12", "/usr/local/bin/python3.12"])
            ensureBrewPackage("sox", binary: "sox", searchPaths: ["/opt/homebrew/bin/sox", "/usr/local/bin/sox"])
            emit("")
        }

        // 3. Find or clone the repo
        let repoDir = try findOrCloneRepo()
        FileManager.default.changeCurrentDirectoryPath(repoDir)
        emit("repo:    \(repoDir)\n")

        // 4. Configure
        emit("--- configure ---")
        fflush(stdout)
        let confStatus = Shell.exec([breezeBin, "configure"], cwd: URL(fileURLWithPath: repoDir))
        guard confStatus == 0 else {
            throw ValidationError("configure failed")
        }

        // 5. Download
        emit("\n--- download ---")
        fflush(stdout)
        let dlStatus = Shell.exec([breezeBin, "download"], cwd: URL(fileURLWithPath: repoDir))
        guard dlStatus == 0 else {
            throw ValidationError("download failed")
        }

        // 6. Done (or launch)
        emit("\n=== Setup complete ===")
        emit("Server:  cd \(repoDir) && breeze launch")
        emit("Test:    curl http://127.0.0.1:7860/health")

        if launch {
            print("\nStarting server...\n")
            let pyPath = URL(fileURLWithPath: repoDir).appending(path: ".venv/bin/python").path(percentEncoded: false)
            let apiPath = URL(fileURLWithPath: repoDir).appending(path: "runtimes/mlx/mlx_api.py").path(percentEncoded: false)

            let env = Env.read(URL(fileURLWithPath: repoDir).appending(path: ".env"))
            for (k, v) in env { setenv(k, v, 1) }

            if FileManager.default.isExecutableFile(atPath: pyPath) {
                let cArgs = [pyPath, apiPath].map { strdup($0) } + [nil]
                execv(pyPath, cArgs)
                perror("execv")
            } else {
                Shell.exec([breezeBin, "launch"], cwd: URL(fileURLWithPath: repoDir))
            }
        }
    }

    // MARK: - Binary path

    private func resolveBreezeBinary() -> String {
        let arg0 = ProcessInfo.processInfo.arguments[0]
        if arg0.hasPrefix("/") { return arg0 }
        let cwd = FileManager.default.currentDirectoryPath
        let resolved = URL(fileURLWithPath: cwd).appending(path: arg0).path(percentEncoded: false)
        if FileManager.default.isExecutableFile(atPath: resolved) { return resolved }
        let (which, status) = Shell.run(["which", "breeze"])
        if status == 0 { return which }
        return arg0
    }

    // MARK: - Homebrew

    private func ensureBrew() {
        let (_, status) = Shell.run(["which", "brew"])
        guard status == 0 else {
            fputs("error: Homebrew not found. Install from https://brew.sh first.\n", stderr)
            Foundation.exit(1)
        }
        print("brew:    ok")
    }

    private func ensureBrewPackage(_ formula: String, binary: String, searchPaths: [String]) {
        for p in searchPaths {
            if FileManager.default.isExecutableFile(atPath: p) {
                print("\(binary.padding(toLength: 8, withPad: " ", startingAt: 0)) ok (\(p))")
                return
            }
        }
        let (_, which) = Shell.run(["which", binary])
        if which == 0 {
            print("\(binary.padding(toLength: 8, withPad: " ", startingAt: 0)) ok")
            return
        }
        print("\(binary.padding(toLength: 8, withPad: " ", startingAt: 0)) not found, installing...")
        let s = Shell.exec(["/opt/homebrew/bin/brew", "install", formula])
        guard s == 0 else {
            fputs("error: brew install \(formula) failed\n", stderr)
            Foundation.exit(1)
        }
    }

    // MARK: - Repo

    private func findOrCloneRepo() throws -> String {
        var dir = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        for _ in 0..<10 {
            if FileManager.default.fileExists(atPath: dir.appending(path: ".env.example").path(percentEncoded: false)) {
                return dir.path(percentEncoded: false)
            }
            dir = dir.deletingLastPathComponent()
        }

        print("Cloning repository...")
        let dest = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
            .appending(path: "agent-land-breeze-tts-api")
        let s = Shell.exec(["/usr/bin/env", "git", "clone", "--recurse-submodules",
                            Self.repoURL, dest.path(percentEncoded: false)])
        guard s == 0 else {
            throw ValidationError("git clone failed")
        }
        return dest.path(percentEncoded: false)
    }
}
