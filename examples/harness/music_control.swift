import Foundation
import AppKit
import ApplicationServices

// Direct AX menu actions in NetEase only. No global keystrokes or media toggle.
let playerBundle = "com.netease.163music"
let command = CommandLine.arguments.dropFirst().first ?? "inspect"

func output(_ value: [String: Any], code: Int32 = 0) -> Never {
    if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) {
        print(String(decoding: data, as: UTF8.self))
    }
    exit(code)
}
func failure(_ message: String, status: String = "failed") -> Never {
    output(["ok": false, "status": status, "message": message], code: 1)
}
func attr(_ node: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(node, name as CFString, &value) == .success else { return nil }
    return value
}
func children(_ node: AXUIElement) -> [AXUIElement] {
    attr(node, kAXChildrenAttribute) as? [AXUIElement] ?? []
}
func title(_ node: AXUIElement) -> String { attr(node, kAXTitleAttribute) as? String ?? "" }
func enabled(_ node: AXUIElement) -> Bool { (attr(node, kAXEnabledAttribute) as? Bool) ?? false }

let appPath = URL(fileURLWithPath: CommandLine.arguments[0]).deletingLastPathComponent()
    .deletingLastPathComponent().deletingLastPathComponent().path
if command == "permission" {
    // Prompt for this fixed helper, never edit the system permission database.
    let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
    _ = AXIsProcessTrustedWithOptions(options)
    NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")!)
    output(["ok": AXIsProcessTrusted(), "app_path": appPath, "status": "permission_requested"])
}
guard AXIsProcessTrusted() else {
    failure("请在系统设置的辅助功能中允许“小米音箱音乐控制”，再重试。", status: "permission_required")
}
guard let running = NSRunningApplication.runningApplications(withBundleIdentifier: playerBundle).first else {
    failure("网易云没有运行，请先点播一首本地歌曲。", status: "not_running")
}
let app = AXUIElementCreateApplication(running.processIdentifier)
AXUIElementSetMessagingTimeout(app, 2)
// Chromium apps expose the dynamic menu before it is visually expanded.
_ = AXUIElementSetAttributeValue(app, "AXManualAccessibility" as CFString, kCFBooleanTrue)
_ = AXUIElementSetAttributeValue(app, "AXEnhancedUserInterface" as CFString, kCFBooleanTrue)

func items() -> [AXUIElement] {
    guard let value = attr(app, kAXMenuBarAttribute), CFGetTypeID(value) == AXUIElementGetTypeID() else { return [] }
    let bar = unsafeBitCast(value, to: AXUIElement.self)
    guard let control = children(bar).first(where: { ["控制", "Controls", "Control"].contains(title($0)) }) else { return [] }
    return children(control).flatMap { children($0) }
}
let pauseTitles = ["暂停", "暂停播放", "Pause"]
let playTitles = ["播放", "继续播放", "Play", "Resume"]
func state(_ list: [AXUIElement]) -> String {
    if list.contains(where: { pauseTitles.contains(title($0)) && enabled($0) }) { return "playing" }
    if list.contains(where: { playTitles.contains(title($0)) && enabled($0) }) { return "paused" }
    return "unknown"
}
let list = items()
if command == "inspect" {
    output(["ok": !list.isEmpty, "state": state(list), "items": list.map { ["title": title($0), "enabled": enabled($0)] as [String: Any] }])
}
guard ["pause", "resume"].contains(command) else { failure("Unsupported player command") }
let desired = command == "pause" ? "paused" : "playing"
if state(list) == desired {
    output(["ok": true, "status": "unchanged", "state": desired])
}
let wanted = command == "pause" ? pauseTitles : playTitles
guard let item = list.first(where: { wanted.contains(title($0)) && enabled($0) }) else {
    failure("没有找到网易云可用的播放控制，请先点播歌曲。", status: "unavailable")
}
guard AXUIElementPerformAction(item, kAXPressAction as CFString) == .success else {
    failure("网易云未接受播放控制，请在电脑检查播放器。")
}
for _ in 0..<15 {
    RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.1))
    if state(items()) == desired { output(["ok": true, "status": "controlled", "state": desired]) }
}
failure("已发送控制，但未确认网易云状态改变，请在电脑检查。", status: "unconfirmed")
