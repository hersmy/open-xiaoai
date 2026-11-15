import Foundation
import CoreAudio

struct Output: Codable {
    let id: AudioDeviceID
    let uid: String
    let name: String
}

// This policy is independent of CoreAudio so connection races can be tested.
struct RoutingPolicy {
    enum Phase { case absent, acquiring, active, yielded, failed }
    var phase: Phase = .absent
    var previous: String?
    var lastAbsentOutput: String?
    var attempts = 0

    mutating func update(target: String?, current: String?, available: Set<String>) -> String? {
        guard let target else {
            let shouldRestore = phase == .active || (phase == .acquiring && attempts > 0)
            phase = .absent
            attempts = 0
            if shouldRestore, let previous, available.contains(previous), current != previous {
                lastAbsentOutput = previous
                return previous
            }
            if let current, available.contains(current) { lastAbsentOutput = current }
            return nil
        }
        if phase == .absent {
            if let current, current != target { previous = current }
            else { previous = lastAbsentOutput ?? previous }
            phase = .acquiring
            attempts = 0
        }
        if phase == .acquiring {
            if current == target { phase = .active; return nil }
            if attempts >= 5 { phase = .failed; return nil }
            attempts += 1
            return target
        }
        if phase == .active, let current, current != target { phase = .yielded }
        return nil
    }
}

struct AudioFailure: Error, CustomStringConvertible {
    let operation: String
    let status: OSStatus
    var description: String { "\(operation): OSStatus=\(status)" }
}

enum Audio {
    static let system = AudioObjectID(kAudioObjectSystemObject)
    static func address(_ selector: AudioObjectPropertySelector,
                        scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> AudioObjectPropertyAddress {
        AudioObjectPropertyAddress(mSelector: selector, mScope: scope, mElement: kAudioObjectPropertyElementMain)
    }
    static func uint(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector) -> UInt32? {
        var addr = address(selector)
        var value: UInt32 = 0
        var size = UInt32(MemoryLayout<UInt32>.size)
        guard AudioObjectGetPropertyData(object, &addr, 0, nil, &size, &value) == noErr else { return nil }
        return value
    }
    static func string(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector) -> String? {
        var addr = address(selector)
        var value: Unmanaged<CFString>?
        var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        guard AudioObjectGetPropertyData(object, &addr, 0, nil, &size, &value) == noErr else { return nil }
        return value?.takeRetainedValue() as String?
    }
    static func outputs() throws -> [Output] {
        var addr = address(kAudioHardwarePropertyDevices)
        var size: UInt32 = 0
        var status = AudioObjectGetPropertyDataSize(system, &addr, 0, nil, &size)
        guard status == noErr else { throw AudioFailure(operation: "list size", status: status) }
        var ids = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
        if ids.isEmpty { return [] }
        status = ids.withUnsafeMutableBytes {
            AudioObjectGetPropertyData(system, &addr, 0, nil, &size, $0.baseAddress!)
        }
        guard status == noErr else { throw AudioFailure(operation: "list outputs", status: status) }
        return ids.compactMap { id in
            guard uint(id, kAudioDevicePropertyDeviceIsAlive) == 1 else { return nil }
            var streams = address(kAudioDevicePropertyStreams, scope: kAudioDevicePropertyScopeOutput)
            var streamSize: UInt32 = 0
            guard AudioObjectGetPropertyDataSize(id, &streams, 0, nil, &streamSize) == noErr,
                  streamSize > 0, let uid = string(id, kAudioDevicePropertyDeviceUID),
                  let name = string(id, kAudioObjectPropertyName) else { return nil }
            return Output(id: id, uid: uid, name: name)
        }
    }
    static func current() -> AudioDeviceID? { uint(system, kAudioHardwarePropertyDefaultOutputDevice) }
    static func select(_ output: Output) throws {
        var addr = address(kAudioHardwarePropertyDefaultOutputDevice)
        var id = output.id
        let status = AudioObjectSetPropertyData(system, &addr, 0, nil,
                                                UInt32(MemoryLayout<AudioDeviceID>.size), &id)
        guard status == noErr else { throw AudioFailure(operation: "select \(output.name)", status: status) }
    }
}

struct SavedState: Codable { var previous: String?; var lastAbsentOutput: String? }

final class Monitor {
    let targetName: String
    let stateFile: URL
    var policy = RoutingPolicy()
    var pending: DispatchWorkItem?
    var timer: DispatchSourceTimer?
    var lastSummary = ""
    var lastState: Data?
    var lastError: String?

    init(targetName: String, stateFile: URL) {
        self.targetName = targetName
        self.stateFile = stateFile
        if let data = try? Data(contentsOf: stateFile), let saved = try? JSONDecoder().decode(SavedState.self, from: data) {
            policy.previous = saved.previous
            policy.lastAbsentOutput = saved.lastAbsentOutput
            lastState = data
        }
    }
    func log(_ text: String) {
        FileHandle.standardError.write(Data("\(ISO8601DateFormatter().string(from: Date())) \(text)\n".utf8))
    }
    func schedule() {
        pending?.cancel()
        let task = DispatchWorkItem { [weak self] in self?.check() }
        pending = task
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4, execute: task)
    }
    func save() throws {
        let data = try JSONEncoder().encode(SavedState(previous: policy.previous, lastAbsentOutput: policy.lastAbsentOutput))
        guard data != lastState else { return }
        try data.write(to: stateFile, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: stateFile.path)
        lastState = data
    }
    func check() {
        do {
            let outputs = try Audio.outputs()
            let current = outputs.first { $0.id == Audio.current() }
            let matches = outputs.filter { $0.name == targetName }
            // Do not guess between identically named devices.
            guard matches.count <= 1 else { throw AudioFailure(operation: "ambiguous target", status: -1) }
            let action = policy.update(target: matches.first?.uid, current: current?.uid,
                                       available: Set(outputs.map(\.uid)))
            let summary = "phase=\(policy.phase) target_available=\(!matches.isEmpty) output=\(current?.name ?? "none")"
            if summary != lastSummary { log(summary); lastSummary = summary }
            try save()
            if let uid = action, let output = outputs.first(where: { $0.uid == uid }) {
                do {
                    try Audio.select(output)
                    log("Requested output: \(output.name)")
                } catch { log("Switch failed: \(error)") }
            }
            if policy.phase == .acquiring {
                DispatchQueue.main.asyncAfter(deadline: .now() + 1) { [weak self] in self?.schedule() }
            }
            lastError = nil
        } catch {
            let message = String(describing: error)
            if message != lastError { log("Check failed: \(message)"); lastError = message }
        }
    }
    func run() throws -> Never {
        for selector in [kAudioHardwarePropertyDevices, kAudioHardwarePropertyDefaultOutputDevice,
                         kAudioHardwarePropertyServiceRestarted] {
            var addr = Audio.address(selector)
            let status = AudioObjectAddPropertyListenerBlock(Audio.system, &addr, DispatchQueue.main) { [weak self] _, _ in self?.schedule() }
            guard status == noErr else { throw AudioFailure(operation: "listen", status: status) }
        }
        // Fallback for delayed Bluetooth readiness or wake from sleep. No subprocess polling.
        let timer = DispatchSource.makeTimerSource(queue: .main)
        timer.schedule(deadline: .now() + 10, repeating: 10, leeway: .seconds(2))
        timer.setEventHandler { [weak self] in self?.schedule() }
        self.timer = timer
        timer.resume()
        log("Started; target=\(targetName)")
        check()
        dispatchMain()
    }
}

func selfTest() {
    var checks = 0
    func expect(_ condition: Bool, _ description: String) {
        guard condition else { fputs("FAIL: \(description)\n", stderr); exit(1) }
        checks += 1
    }
    let all: Set<String> = ["display", "headset", "speakers"]
    var p = RoutingPolicy()
    expect(p.update(target: nil, current: "display", available: all) == nil, "idle does not change audio")
    expect(p.update(target: "headset", current: "display", available: all) == "headset", "connect selects target")
    expect(p.update(target: "headset", current: "headset", available: all) == nil, "acknowledge selection")
    expect(p.update(target: "headset", current: "speakers", available: all) == nil && p.phase == .yielded, "respect manual change")
    expect(p.update(target: "headset", current: "speakers", available: all) == nil, "do not steal audio back")
    expect(p.update(target: nil, current: "speakers", available: ["display", "speakers"]) == nil, "disconnect preserves manual route")
    expect(p.update(target: "headset", current: "speakers", available: all) == "headset", "reconnect re-arms")
    _ = p.update(target: "headset", current: "headset", available: all)
    expect(p.update(target: nil, current: "display", available: ["display", "speakers"]) == "speakers", "restore previous despite OS fallback")
    var race = RoutingPolicy()
    _ = race.update(target: nil, current: "display", available: all)
    expect(race.update(target: "headset", current: "headset", available: all) == nil && race.previous == "display", "OS switches before listener")
    expect(race.update(target: nil, current: "speakers", available: ["speakers"]) == nil, "unavailable prior output is not selected")
    var retry = RoutingPolicy()
    for _ in 0..<5 { expect(retry.update(target: "headset", current: "display", available: all) == "headset", "retry transient selection error") }
    expect(retry.update(target: "headset", current: "display", available: all) == nil && retry.phase == .failed, "bound failed retries")
    var restart = RoutingPolicy(previous: "display", lastAbsentOutput: "display")
    _ = restart.update(target: "headset", current: "headset", available: all)
    expect(restart.update(target: nil, current: "speakers", available: all) == "display", "restore across process restart")
    print("PASS: \(checks) routing checks")
}

func argument(_ flag: String) -> String? {
    let args = CommandLine.arguments
    guard let i = args.firstIndex(of: flag), i + 1 < args.count else { return nil }
    return args[i + 1]
}

do {
    if CommandLine.arguments.contains("--self-test") { selfTest() }
    else if CommandLine.arguments.contains("--list") {
        let outputs = try Audio.outputs()
        let payload: [String: Any] = ["outputs": try outputs.map { try JSONSerialization.jsonObject(with: JSONEncoder().encode($0)) },
                                      "default_output_id": Audio.current() ?? kAudioObjectUnknown]
        let data = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys])
        print(String(decoding: data, as: UTF8.self))
    } else {
        guard let name = argument("--target"), let state = argument("--state") else {
            fputs("Usage: headphone-output --list | --self-test | --target NAME --state FILE\n", stderr)
            exit(2)
        }
        let monitor = Monitor(targetName: name, stateFile: URL(fileURLWithPath: state))
        try monitor.run()
    }
} catch { fputs("\(error)\n", stderr); exit(1) }
