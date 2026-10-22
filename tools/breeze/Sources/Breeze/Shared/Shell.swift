import Foundation

enum Shell {
    @discardableResult
    static func capture(_ args: String...) -> String {
        capture(Array(args))
    }

    @discardableResult
    static func capture(_ args: [String]) -> String {
        let (out, _) = run(args)
        return out
    }

    static func run(_ args: [String]) -> (String, Int32) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        p.arguments = args
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = Pipe()
        do { try p.run() } catch { return ("", 1) }
        p.waitUntilExit()
        let out = (String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return (out, p.terminationStatus)
    }

    @discardableResult
    static func exec(_ args: [String], cwd: URL? = nil, passthrough: Bool = true) -> Int32 {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: args[0])
        p.arguments = Array(args.dropFirst())
        if let cwd { p.currentDirectoryURL = cwd }
        if !passthrough {
            p.standardOutput = Pipe()
            p.standardError = Pipe()
        }
        do { try p.run() } catch {
            fputs("Failed to run \(args[0]): \(error)\n", stderr)
            return 1
        }
        p.waitUntilExit()
        return p.terminationStatus
    }
}
