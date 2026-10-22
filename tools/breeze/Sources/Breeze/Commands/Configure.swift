import ArgumentParser
import Foundation

struct Configure: ParsableCommand {
    static let configuration = CommandConfiguration(
        abstract: "Detect Apple Silicon hardware and write MLX settings into .env."
    )

    @Option(name: .long, help: "Model variant: int8 or auto.")
    var model: String = "auto"

    @Flag(name: .long, help: "Print settings without writing .env.")
    var dryRun = false

    struct MLXModel {
        let repo, dir, desc: String
        let minGB: Int
    }

    static let models: [String: MLXModel] = [
        "int8": MLXModel(
            repo: "rishikksh20/Breeze-TTS-2-mlx", dir: "mlx-int8",
            desc: "INT8 quantized (3.72 GB, ~4 GB unified)", minGB: 8
        ),
    ]

    func run() throws {
        Hardware.requireARM64()

        guard ["int8", "auto"].contains(model) else {
            throw ValidationError("--model must be int8 or auto")
        }

        let chip = Hardware.detect()
        print("Chip:    \(chip.name)")
        print("Memory:  \(chip.memGB) GB unified")
        print("GPU:     \(chip.gpuCores > 0 ? "\(chip.gpuCores) cores" : "(detecting)")")
        print("ANE:     \(chip.aneCores > 0 ? "\(chip.aneCores) cores" : "present")")
        print("macOS:   \(chip.macOS)")

        let selected = "int8"
        if model == "auto" {
            print("\nauto-selected model: int8 (\(chip.memGB) GB memory)")
        }

        guard let m = Self.models[selected] else {
            throw ValidationError("Unknown model: \(selected)")
        }
        if chip.memGB < m.minGB {
            print("warning: \(selected) needs \(m.minGB)+ GB; you have \(chip.memGB) GB")
        }
        print("Model:   \(m.desc)\n")

        let changes: [String: String] = [
            "COMPOSE_PROFILES": "mlx",
            "BREEZE_MLX_REPO": m.repo,
            "BREEZE_MLX_MODEL_DIR": m.dir,
            "BREEZE_MLX_COMPILE": "1",
            "BREEZE_MLX_FAST_FIRST": "6",
            "BREEZE_MLX_CACHE_GB": "1",
        ]

        print("Settings:")
        for (k, v) in changes.sorted(by: { $0.key < $1.key }) { print("  \(k)=\(v)") }

        if dryRun {
            print("\n(dry run: .env not changed)")
        } else {
            Env.write(changes)
            print("\nWrote \(Repo.envFile.path(percentEncoded: false))")
            print("Next: breeze download")
        }
    }
}
