import AppKit
import Foundation

let arguments = Array(CommandLine.arguments.dropFirst())
guard arguments.count >= 2 else {
    fputs("Usage: app-services APP_BUNDLE_ID SERVICE_LABEL...\n", stderr)
    exit(2)
}

nonisolated func launchctl(_ arguments: [String]) {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
    process.arguments = arguments
    process.standardOutput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    do {
        try process.run()
        process.waitUntilExit()
        if process.terminationStatus != 0 && arguments[0] == "kickstart" {
            fputs("launchctl \(arguments.joined(separator: " ")): exit \(process.terminationStatus)\n", stderr)
        }
    } catch {
        fputs("launchctl: \(error)\n", stderr)
    }
}

@MainActor final class AppServices {
    let bundleID: String
    let services: [String]
    let domain = "gui/\(getuid())"
    var wasRunning: Bool?

    init(bundleID: String, services: [String]) {
        self.bundleID = bundleID
        self.services = services
    }

    func reconcile() {
        let running = NSWorkspace.shared.runningApplications.contains {
            $0.bundleIdentifier == bundleID && !$0.isTerminated
        }
        if running {
            // Without -k, kickstart leaves a running service alone and restarts an exited one.
            for service in services { launchctl(["kickstart", "\(domain)/\(service)"]) }
        } else if wasRunning != false {
            for service in services { launchctl(["kill", "SIGTERM", "\(domain)/\(service)"]) }
        }
        if wasRunning != running {
            print("\(bundleID): \(running ? "running" : "stopped")")
            fflush(stdout)
        }
        wasRunning = running
    }

}

let supervisor = AppServices(bundleID: arguments[0], services: Array(arguments.dropFirst()))
let center = NSWorkspace.shared.notificationCenter
let observers = [NSWorkspace.didLaunchApplicationNotification, NSWorkspace.didTerminateApplicationNotification].map {
    center.addObserver(forName: $0, object: nil, queue: .main) { _ in MainActor.assumeIsolated { supervisor.reconcile() } }
}
// Reconcile after missed workspace events and recover services that exit unexpectedly.
let timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { _ in MainActor.assumeIsolated { supervisor.reconcile() } }
supervisor.reconcile()
RunLoop.main.run()
