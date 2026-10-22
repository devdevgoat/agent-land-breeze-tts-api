import ArgumentParser
import Foundation

struct Download: ParsableCommand {
    static let configuration = CommandConfiguration(
        abstract: "Fetch MLX model weights from HuggingFace."
    )

    @Flag(name: .long, help: "Re-download even if files are present.")
    var force = false

    func run() throws {
        let cfg = Env.settings()
        let runtime = cfg["COMPOSE_PROFILES"] ?? "gguf"

        guard runtime == "mlx" else {
            throw ValidationError(
                "COMPOSE_PROFILES=\(runtime), not mlx. Run `breeze configure` first.")
        }

        let hfRepo = cfg["BREEZE_MLX_REPO"] ?? "rishikksh20/Breeze-TTS-2-mlx"
        let modelDir = cfg["BREEZE_MLX_MODEL_DIR"] ?? "mlx-int8"
        var modelsRoot = cfg["BREEZE_MODELS_DIR"] ?? "./models"
        if !modelsRoot.hasPrefix("/") {
            modelsRoot = Repo.root.appending(path: modelsRoot).path(percentEncoded: false)
        }
        let destDir = URL(fileURLWithPath: modelsRoot).appending(path: modelDir)

        if !force && FileManager.default.fileExists(atPath: destDir.path(percentEncoded: false)) {
            let contents = (try? FileManager.default.contentsOfDirectory(
                atPath: destDir.path(percentEncoded: false))) ?? []
            if contents.contains(where: { $0.hasSuffix(".safetensors") }) {
                print("Model already downloaded at \(destDir.path(percentEncoded: false))")
                print("Use --force to re-download.")
                return
            }
        }

        print("Repository: \(hfRepo)")
        print("Destination: \(destDir.path(percentEncoded: false))")
        print("Listing files...")

        let files = listFiles(hfRepo: hfRepo)
        let totalSize = files.reduce(0) { $0 + $1.size }
        print("Found \(files.count) files (\(humanSize(totalSize)))\n")

        for (i, file) in files.enumerated() {
            let dest = destDir.appending(path: file.path)
            let exists = FileManager.default.fileExists(atPath: dest.path(percentEncoded: false))
            if exists && !force {
                print("[\(i + 1)/\(files.count)] \(file.path) (\(humanSize(file.size))) -- already present")
                continue
            }
            print("[\(i + 1)/\(files.count)] \(file.path) (\(humanSize(file.size)))")
            downloadFile(hfRepo: hfRepo, file: file.path, dest: dest)
        }

        let finalContents = (try? FileManager.default.contentsOfDirectory(
            atPath: destDir.path(percentEncoded: false))) ?? []
        let safetensors = finalContents.filter { $0.hasSuffix(".safetensors") }
        guard !safetensors.isEmpty else {
            throw ValidationError("No .safetensors files found in \(destDir.path(percentEncoded: false))")
        }

        print("\nDone. \(safetensors.count) safetensors file(s) in \(destDir.path(percentEncoded: false))")
        print("Next: breeze launch")
    }

    // MARK: - HuggingFace API

    private func listFiles(hfRepo: String) -> [(path: String, size: Int)] {
        let url = "https://huggingface.co/api/models/\(hfRepo)"
        let (json, status) = Shell.run(["curl", "-fsSL", url])
        guard status == 0,
              let data = json.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let siblings = obj["siblings"] as? [[String: Any]]
        else {
            fputs("Failed to list files for \(hfRepo). Check the repo name and network.\n", stderr)
            Foundation.exit(1)
        }
        return siblings.compactMap { sib in
            guard let path = sib["rfilename"] as? String else { return nil }
            let size: Int
            if let lfs = sib["lfs"] as? [String: Any], let lfsSize = lfs["size"] as? Int {
                size = lfsSize
            } else {
                size = sib["size"] as? Int ?? 0
            }
            return (path, size)
        }.filter { !$0.path.hasPrefix(".git") }
    }

    private func humanSize(_ bytes: Int) -> String {
        if bytes >= 1_000_000_000 { return String(format: "%.2f GB", Double(bytes) / 1e9) }
        if bytes >= 1_000_000 { return String(format: "%.1f MB", Double(bytes) / 1e6) }
        if bytes >= 1_000 { return String(format: "%.0f KB", Double(bytes) / 1e3) }
        return "\(bytes) B"
    }

    private func downloadFile(hfRepo: String, file: String, dest: URL) {
        let dir = dest.deletingLastPathComponent()
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)

        let url = "https://huggingface.co/\(hfRepo)/resolve/main/\(file)?download=true"
        var args = ["curl", "-fSL", "--progress-bar", "-C", "-", "--create-dirs",
                    "-o", dest.path(percentEncoded: false)]
        if let token = ProcessInfo.processInfo.environment["HF_TOKEN"] {
            args += ["-H", "Authorization: Bearer \(token)"]
        }
        args.append(url)

        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        p.arguments = args
        do { try p.run() } catch {
            fputs("  failed to start curl\n", stderr)
            return
        }
        p.waitUntilExit()
        if p.terminationStatus != 0 {
            fputs("  download failed (curl exit \(p.terminationStatus))\n", stderr)
        }
    }
}
