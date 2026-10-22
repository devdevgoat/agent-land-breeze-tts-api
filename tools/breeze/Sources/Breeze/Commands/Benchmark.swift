import ArgumentParser
import Foundation

struct Benchmark: ParsableCommand {
    static let configuration = CommandConfiguration(
        abstract: "Measure a running Breeze TTS server on Apple Silicon."
    )

    @Option(name: .long, help: "Server URL (default: from .env).")
    var url: String?

    @Option(name: .long, help: "Number of iterations.")
    var runs: Int = 3

    @Option(name: .long, help: "Label for results (e.g. 'M4, mlx').")
    var label: String = ""

    @Option(name: .long, help: "Notes to include in results.")
    var notes: String = ""

    private static let bytesPerSecond = 24000 * 2

    private static let shortText =
        "The deployment finished a minute ago and every check came back green, so we are good to go."
    private static let longText =
        "I just finished reviewing the deployment logs, and everything looks healthy. The new build passed all tests, response times dropped by about twenty percent, and there were no errors overnight. I would suggest we roll it out to the remaining servers this afternoon, then keep an eye on memory usage until tomorrow morning."

    struct SpeechResult {
        let ttfa: Double
        let wall: Double
        let audio: Double
        var rtf: Double { wall / max(audio, 0.001) }
    }

    struct WarmResult {
        let idMs: Int
        let waitFirstAudio: Double
        let audio: Double
        let wall: Double
    }

    func run() throws {
        let cfg = Env.settings()
        let serverURL = url ?? "http://127.0.0.1:\(cfg["BREEZE_PORT"] ?? "7860")"

        let (healthOut, healthStatus) = Shell.run(["curl", "-fsS", "\(serverURL)/health"])
        guard healthStatus == 0 else {
            throw ValidationError("Server not reachable at \(serverURL)/health")
        }
        print("Server: \(serverURL) -- \(healthOut)")

        let hw = Hardware.detect()
        print("Hardware: \(hw.name), \(hw.memGB) GB, \(hw.gpuCores) GPU cores\n")

        print("Warming up...")
        _ = measureSpeech(url: serverURL, text: Self.shortText)

        print("Running \(runs) iterations...")
        var shortResults: [SpeechResult] = []
        var longResults: [SpeechResult] = []
        for n in 1...runs {
            print("  short \(n)/\(runs)")
            if let r = measureSpeech(url: serverURL, text: Self.shortText) { shortResults.append(r) }
            print("  long  \(n)/\(runs)")
            if let r = measureSpeech(url: serverURL, text: Self.longText) { longResults.append(r) }
        }

        var warmResults: [WarmResult] = []
        for n in 1...max(1, runs - 1) {
            print("  warm  \(n)/\(max(1, runs - 1))")
            if let r = measureWarm(url: serverURL, text: Self.longText) { warmResults.append(r) }
        }

        saveResults(
            serverURL: serverURL, hw: hw, cfg: cfg,
            shortResults: shortResults, longResults: longResults, warmResults: warmResults
        )

        printTable(shortResults: shortResults, longResults: longResults, warmResults: warmResults, hw: hw)
    }

    // MARK: - Measurements

    private func measureSpeech(url: String, text: String) -> SpeechResult? {
        let writeOut = #"{"ttfa":%{time_starttransfer},"wall":%{time_total},"size":%{size_download}}"#
        let body = "text=\(text)&instruction=A+calm,+friendly+adult+voice+speaking+clearly.&cfg_scale=4&seed=7"
        let (out, status) = Shell.run([
            "curl", "-sS", "-w", writeOut, "-o", "/dev/null",
            "-X", "POST", "-d", body, "\(url)/v1/audio/speech",
        ])
        guard status == 0, let data = out.data(using: .utf8),
              let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let ttfa = j["ttfa"] as? Double,
              let wall = j["wall"] as? Double,
              let size = j["size"] as? Double
        else { return nil }
        let audio = size / Double(Self.bytesPerSecond)
        return SpeechResult(ttfa: ttfa, wall: wall, audio: audio)
    }

    private func measureWarm(url: String, text: String) -> WarmResult? {
        let body = "text=\(text)&instruction=A+calm,+friendly+adult+voice+speaking+clearly.&cfg_scale=4&seed=7"
        let t0 = CFAbsoluteTimeGetCurrent()
        let (warmOut, warmStatus) = Shell.run([
            "curl", "-sS", "-X", "POST", "-d", body, "\(url)/v1/audio/speech/warm",
        ])
        guard warmStatus == 0 else { return nil }
        let idMs = Int((CFAbsoluteTimeGetCurrent() - t0) * 1000)
        guard let data = warmOut.data(using: .utf8),
              let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let respURL = j["url"] as? String
        else { return nil }

        let writeOut = #"{"ttfa":%{time_starttransfer},"wall":%{time_total},"size":%{size_download}}"#
        let (out, status) = Shell.run([
            "curl", "-sS", "-w", writeOut, "-o", "/dev/null", "\(url)\(respURL)",
        ])
        guard status == 0, let d2 = out.data(using: .utf8),
              let j2 = try? JSONSerialization.jsonObject(with: d2) as? [String: Any],
              let ttfa = j2["ttfa"] as? Double,
              let wall = j2["wall"] as? Double,
              let size = j2["size"] as? Double
        else { return nil }
        let audio = max(0, size - 44) / Double(Self.bytesPerSecond)
        return WarmResult(idMs: idMs, waitFirstAudio: ttfa, audio: audio, wall: wall)
    }

    // MARK: - Output

    private func rangeStr(_ values: [Double]) -> String {
        guard let lo = values.min(), let hi = values.max() else { return "-" }
        if values.count == 1 { return String(format: "%.3g", lo) }
        return String(format: "%.3g-%.3g", lo, hi)
    }

    private func saveResults(
        serverURL: String, hw: ChipInfo, cfg: [String: String],
        shortResults: [SpeechResult], longResults: [SpeechResult], warmResults: [WarmResult]
    ) {
        let configKeys = ["COMPOSE_PROFILES", "BREEZE_MLX_REPO", "BREEZE_MLX_MODEL_DIR", "BREEZE_MLX_COMPILE"]
        var config: [String: String] = [:]
        for k in configKeys { if let v = cfg[k] { config[k] = v } }

        let pv = ProcessInfo.processInfo.operatingSystemVersion
        let results: [String: Any] = [
            "date": ISO8601DateFormatter().string(from: Date()),
            "label": label, "notes": notes, "url": serverURL,
            "system": [
                "chip": hw.name, "gpu": "\(hw.name) (\(hw.gpuCores) GPU cores)",
                "memory_gb": hw.memGB,
                "os": "macOS \(pv.majorVersion).\(pv.minorVersion).\(pv.patchVersion)",
            ] as [String: Any],
            "config": config,
            "short_line": shortResults.map {
                ["ttfa_s": round($0.ttfa * 1000) / 1000, "wall_s": round($0.wall * 100) / 100,
                 "audio_s": round($0.audio * 100) / 100, "rtf": round($0.rtf * 1000) / 1000]
            },
            "words_50": longResults.map {
                ["ttfa_s": round($0.ttfa * 1000) / 1000, "wall_s": round($0.wall * 100) / 100,
                 "audio_s": round($0.audio * 100) / 100, "rtf": round($0.rtf * 1000) / 1000]
            },
            "warm": warmResults.map {
                ["id_ms": $0.idMs, "wait_first_audio_s": round($0.waitFirstAudio * 100) / 100,
                 "audio_s": round($0.audio * 100) / 100, "wall_s": round($0.wall * 100) / 100]
                    as [String: Any]
            },
        ]

        let chipSlug = hw.name.lowercased().replacingOccurrences(of: " ", with: "-")
        let runtime = config["COMPOSE_PROFILES"] ?? "mlx"
        let dateStr = String(ISO8601DateFormatter().string(from: Date()).prefix(10))
        let outDir = Repo.root.appending(path: "benchmarks/results")
        try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)
        let outFile = outDir.appending(path: "\(dateStr)_\(chipSlug)_\(runtime).json")

        if let jsonData = try? JSONSerialization.data(
            withJSONObject: results, options: [.prettyPrinted, .sortedKeys])
        {
            try? jsonData.write(to: outFile)
            print("\nSaved \(outFile.path(percentEncoded: false))\n")
        }
    }

    private func printTable(
        shortResults: [SpeechResult], longResults: [SpeechResult],
        warmResults: [WarmResult], hw: ChipInfo
    ) {
        print("| Test | Time to first audio (s) | Wall (s) | Audio (s) | RTF |")
        print("|---|---|---|---|---|")
        print(
            "| short line | \(rangeStr(shortResults.map(\.ttfa))) | \(rangeStr(shortResults.map(\.wall))) | \(rangeStr(shortResults.map(\.audio))) | \(rangeStr(shortResults.map(\.rtf))) |"
        )
        print(
            "| 50 words | \(rangeStr(longResults.map(\.ttfa))) | \(rangeStr(longResults.map(\.wall))) | \(rangeStr(longResults.map(\.audio))) | \(rangeStr(longResults.map(\.rtf))) |"
        )

        if !warmResults.isEmpty {
            print("\n| Warm (50 words) | id (ms) | wait first audio (s) | wall (s) |")
            print("|---|---|---|---|")
            for w in warmResults {
                print(
                    "| | \(w.idMs) | \(String(format: "%.2f", w.waitFirstAudio)) | \(String(format: "%.2f", w.wall)) |"
                )
            }
        }

        print("\nApple Silicon: \(hw.name), \(hw.memGB) GB unified memory, \(hw.gpuCores) GPU cores")
    }
}
