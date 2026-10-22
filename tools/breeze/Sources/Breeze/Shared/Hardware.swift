import Foundation

struct ChipInfo: Sendable {
    let name: String
    let memGB: Int
    let gpuCores: Int
    let aneCores: Int
    let macOS: String
}

enum Hardware {
    static func detect() -> ChipInfo {
        let pv = ProcessInfo.processInfo.operatingSystemVersion
        let macOS = "\(pv.majorVersion).\(pv.minorVersion).\(pv.patchVersion)"
        let name = Shell.capture("sysctl", "-n", "machdep.cpu.brand_string")
        let memGB = Int(UInt64(Shell.capture("sysctl", "-n", "hw.memsize")) ?? 0) / (1024 * 1024 * 1024)

        var gpuCores = 0
        let sp = Shell.capture("system_profiler", "SPDisplaysDataType")
        for line in sp.components(separatedBy: "\n") where line.contains("Total Number of Cores") {
            if let r = line.range(of: #"\d+"#, options: .regularExpression) {
                gpuCores = Int(line[r]) ?? 0
            }
            break
        }

        let ane = Shell.capture("ioreg", "-r", "-c", "AppleARMIODevice", "-n", "ane0")
        let aneCores = ane.lowercased().contains("ane0") ? 16 : 0

        return ChipInfo(
            name: name.isEmpty ? "Apple Silicon" : name,
            memGB: memGB,
            gpuCores: gpuCores,
            aneCores: aneCores,
            macOS: macOS
        )
    }

    static func requireARM64() {
        guard Shell.capture("uname", "-m") == "arm64" else {
            fputs("error: Apple Silicon (ARM64) required. Intel Macs not supported.\n", stderr)
            Foundation.exit(1)
        }
    }
}
