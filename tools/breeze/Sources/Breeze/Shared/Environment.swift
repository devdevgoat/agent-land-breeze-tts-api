import Foundation

enum Repo {
    static func find() -> URL {
        var dir = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        for _ in 0..<10 {
            if FileManager.default.fileExists(atPath: dir.appending(path: ".env.example").path(percentEncoded: false)) {
                return dir
            }
            dir = dir.deletingLastPathComponent()
        }
        fputs("error: cannot find repo root (.env.example). Run from the repo directory.\n", stderr)
        Foundation.exit(1)
    }

    static let root = find()
    static let envFile = root.appending(path: ".env")
    static let envExample = root.appending(path: ".env.example")
}

enum Env {
    static func read(_ url: URL) -> [String: String] {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return [:] }
        var d: [String: String] = [:]
        for line in text.components(separatedBy: "\n") {
            let s = line.trimmingCharacters(in: .whitespaces)
            guard !s.isEmpty, !s.hasPrefix("#"), let i = s.firstIndex(of: "=") else { continue }
            let k = s[..<i].trimmingCharacters(in: .whitespaces)
            var v = String(s[s.index(after: i)...]).trimmingCharacters(in: .whitespaces)
            if (v.hasPrefix("\"") && v.hasSuffix("\"")) || (v.hasPrefix("'") && v.hasSuffix("'")) {
                v = String(v.dropFirst().dropLast())
            }
            d[k] = v
        }
        return d
    }

    static func settings() -> [String: String] {
        var s = read(Repo.envExample)
        for (k, v) in read(Repo.envFile) { s[k] = v }
        return s
    }

    static func write(_ changes: [String: String]) {
        let fm = FileManager.default
        if !fm.fileExists(atPath: Repo.envFile.path(percentEncoded: false)) {
            try! fm.copyItem(at: Repo.envExample, to: Repo.envFile)
        }
        var lines = (try! String(contentsOf: Repo.envFile, encoding: .utf8)).components(separatedBy: "\n")
        var pending = changes
        for i in lines.indices {
            let s = lines[i].trimmingCharacters(in: .whitespaces)
            guard !s.hasPrefix("#"), let eq = s.firstIndex(of: "=") else { continue }
            let k = String(s[..<eq]).trimmingCharacters(in: .whitespaces)
            if let v = pending.removeValue(forKey: k) { lines[i] = "\(k)=\(v)" }
        }
        for (k, v) in pending.sorted(by: { $0.key < $1.key }) { lines.append("\(k)=\(v)") }
        try! lines.joined(separator: "\n").write(to: Repo.envFile, atomically: true, encoding: .utf8)
    }

    static func modelPath() -> URL {
        let cfg = settings()
        var modelsRoot = cfg["BREEZE_MODELS_DIR"] ?? "./models"
        if !modelsRoot.hasPrefix("/") {
            modelsRoot = Repo.root.appending(path: modelsRoot).path(percentEncoded: false)
        }
        let mlxDir = cfg["BREEZE_MLX_MODEL_DIR"] ?? "mlx-int8"
        return URL(fileURLWithPath: modelsRoot).appending(path: mlxDir)
    }
}
