// Окно F5Voice: состояние, сочетание клавиш, стиль плашки, языки, модель, перенос строки,
// пробел, автозапуск, разрешения. Открывается двойным щелчком по F5Voice.app (Launchpad,
// Spotlight, open -a F5Voice), из меню в строке состояния и при запуске не из службы.
// Настройки применяются сразу: каждое изменение пишется в config.json и перечитывается.

import AVFoundation
import Cocoa

let hotkeyPresets = ["F5", "F6", "F13", "F19", "cmd+shift+space", "ctrl+alt+space", "ctrl+alt+d"]
let styleNames = [("glass", "Liquid Glass"), ("metal", "Liquid Metal"), ("clear", "Прозрачное стекло"), ("dark", "Тёмная")]
let newlineNames = [("option", "⌥⏎  Option + Return"), ("shift", "⇧⏎  Shift + Return"), ("none", "⏎  Return")]
let recordModeNames = [("auto", "Нажатие или удержание"), ("toggle", "Только нажатие"), ("hold", "Только удержание")]
let launchAgentPlist = NSHomeDirectory() + "/Library/LaunchAgents/\(launchdLabel).plist"

/// Служба выключена через launchctl disable: при входе в систему не запустится.
func launchAgentDisabled() -> Bool {
    let (code, out) = sh("/bin/launchctl", ["print-disabled", "gui/\(getuid())"])
    guard code == 0 else { return false }
    return out.contains("\"\(launchdLabel)\" => disabled") || out.contains("\"\(launchdLabel)\" => true")
}

final class SettingsWindow: NSObject, NSWindowDelegate, NSTextFieldDelegate, NSTableViewDataSource {
    private let window: NSWindow
    private unowned let app: App
    private let status = NSTextField(wrappingLabelWithString: "")
    private let hotkeyPopup = NSPopUpButton(frame: .zero, pullsDown: false)
    private let recordPopup = NSPopUpButton(frame: .zero, pullsDown: false)
    private let historyTable = NSTableView()
    private var historyItems: [(time: String, text: String)] = []
    private var historyStamp: Date?
    private let stylePopup = NSPopUpButton(frame: .zero, pullsDown: false)
    private let languagesField = NSTextField(string: "")
    private let modelField = NSTextField(string: "")
    private let termPopup = NSPopUpButton(frame: .zero, pullsDown: false)
    private let otherPopup = NSPopUpButton(frame: .zero, pullsDown: false)
    private let trailingBox = NSButton(checkboxWithTitle: "Ставить пробел после надиктованного текста", target: nil, action: nil)
    private let autostartBox = NSButton(checkboxWithTitle: "Запускать при входе в систему", target: nil, action: nil)
    private let micLabel = NSTextField(labelWithString: "")
    private let axLabel = NSTextField(labelWithString: "")
    private var hotkeySpecs: [String] = []
    private var timer: Timer?
    private var updating = false

    init(app: App) {
        self.app = app
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 560, height: 560),
                          styleMask: [.titled, .closable, .miniaturizable],
                          backing: .buffered, defer: false)
        super.init()
        window.title = "F5Voice"
        window.isReleasedWhenClosed = false
        window.delegate = self
        let content = buildContent()
        window.contentView = content
        window.setContentSize(content.fittingSize)
        window.center()
    }

    var isVisible: Bool { window.isVisible }

    /// Пока окно открыто, приложение видно в Dock и ⌘Tab; закрыли — снова только значок в строке меню.
    func show() {
        refresh()
        NSApp.setActivationPolicy(.regular)
        if !window.isVisible { window.center() }
        window.makeKeyAndOrderFront(nil)
        window.makeFirstResponder(nil)  // без курсора в поле «Языки»: окно открылось, чтобы смотреть, а не печатать
        NSApp.activate(ignoringOtherApps: true)
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.refreshStatus() }
    }

    func windowWillClose(_ notification: Notification) {
        timer?.invalidate()
        timer = nil
        commitFields()
        NSApp.setActivationPolicy(.accessory)
    }

    // MARK: Вид

    private func label(_ text: String) -> NSTextField {
        let l = NSTextField(labelWithString: text)
        l.alignment = .right
        l.textColor = .secondaryLabelColor
        return l
    }

    private func section(_ text: String) -> NSTextField {
        let l = NSTextField(labelWithString: text)
        l.font = .systemFont(ofSize: 13, weight: .semibold)
        return l
    }

    private func separator() -> NSView {
        let box = NSBox()
        box.boxType = .separator
        box.widthAnchor.constraint(equalToConstant: 512).isActive = true
        return box
    }

    private func buildContent() -> NSView {
        let icon = NSImageView(image: NSApp.applicationIconImage)
        icon.imageScaling = .scaleProportionallyUpOrDown
        icon.widthAnchor.constraint(equalToConstant: 72).isActive = true
        icon.heightAnchor.constraint(equalToConstant: 72).isActive = true
        let title = NSTextField(labelWithString: "F5Voice")
        title.font = .systemFont(ofSize: 24, weight: .bold)
        status.font = .systemFont(ofSize: 13)
        status.textColor = .secondaryLabelColor
        status.preferredMaxLayoutWidth = 420
        let titles = NSStackView(views: [title, status])
        titles.orientation = .vertical
        titles.alignment = .leading
        titles.spacing = 4
        let header = NSStackView(views: [icon, titles])
        header.orientation = .horizontal
        header.alignment = .centerY
        header.spacing = 16

        hotkeyPopup.target = self
        hotkeyPopup.action = #selector(hotkeyChanged)
        let capture = NSButton(title: "Записать…", target: self, action: #selector(captureHotkey))
        capture.toolTip = "Нажать нужное сочетание на клавиатуре"
        let hotkeyRow = NSStackView(views: [hotkeyPopup, capture])
        hotkeyRow.spacing = 8

        for (name, text) in recordModeNames {
            recordPopup.addItem(withTitle: text)
            recordPopup.lastItem?.representedObject = name
        }
        recordPopup.target = self
        recordPopup.action = #selector(recordModeChanged)
        recordPopup.toolTip = "Нажатие: первое — запись, второе — готово. Удержание: запись, пока держишь клавишу"
        for (name, text) in styleNames {
            stylePopup.addItem(withTitle: text)
            stylePopup.lastItem?.representedObject = name
        }
        stylePopup.target = self
        stylePopup.action = #selector(styleChanged)
        let preview = NSButton(title: "Показать", target: self, action: #selector(previewStyle))
        let styleRow = NSStackView(views: [stylePopup, preview])
        styleRow.spacing = 8

        for field in [languagesField, modelField] {
            field.delegate = self
            field.target = self
            field.action = #selector(fieldChanged)
            field.widthAnchor.constraint(equalToConstant: 300).isActive = true
        }
        languagesField.placeholderString = "ru,en"
        languagesField.toolTip = "Через запятую, первый — основной"
        modelField.placeholderString = "mlx-community/whisper-large-v3-turbo"
        modelField.toolTip = "Модель whisper для mlx; другая скачается при первой диктовке"

        for popup in [termPopup, otherPopup] {
            for (name, text) in newlineNames {
                popup.addItem(withTitle: text)
                popup.lastItem?.representedObject = name
            }
            popup.target = self
            popup.action = #selector(newlineChanged(_:))
        }
        for popup in [hotkeyPopup, recordPopup, stylePopup, termPopup, otherPopup] {
            popup.widthAnchor.constraint(equalToConstant: 210).isActive = true
        }
        trailingBox.target = self
        trailingBox.action = #selector(trailingChanged)
        autostartBox.target = self
        autostartBox.action = #selector(autostartChanged)

        let form = NSGridView(views: [
            [label("Сочетание клавиш"), hotkeyRow],
            [label("Запись"), recordPopup],
            [label("Стиль плашки"), styleRow],
            [label("Языки"), languagesField],
            [label("Модель"), modelField],
            [label("Перенос строки в терминале"), termPopup],
            [label("В остальных программах"), otherPopup],
            [NSGridCell.emptyContentView, trailingBox],
            [NSGridCell.emptyContentView, autostartBox],
        ])
        form.rowSpacing = 10
        form.columnSpacing = 12
        form.rowAlignment = .firstBaseline
        form.column(at: 0).xPlacement = .trailing

        let micButton = NSButton(title: "Открыть настройки…", target: self, action: #selector(openMic))
        let axButton = NSButton(title: "Открыть настройки…", target: self, action: #selector(openAX))
        let perms = NSGridView(views: [
            [label("Микрофон"), micLabel, micButton],
            [label("Универсальный доступ"), axLabel, axButton],
        ])
        perms.rowSpacing = 8
        perms.columnSpacing = 12
        perms.rowAlignment = .firstBaseline
        perms.column(at: 0).xPlacement = .trailing
        perms.column(at: 1).width = 190

        let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("text"))
        historyTable.addTableColumn(column)
        historyTable.headerView = nil
        historyTable.rowHeight = 20
        historyTable.usesAlternatingRowBackgroundColors = true
        historyTable.dataSource = self
        historyTable.target = self
        historyTable.doubleAction = #selector(copyHistory)
        let scroll = NSScrollView()
        scroll.documentView = historyTable
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        scroll.widthAnchor.constraint(equalToConstant: 512).isActive = true
        scroll.heightAnchor.constraint(equalToConstant: 104).isActive = true
        let copyButton = NSButton(title: "Скопировать выбранное", target: self, action: #selector(copyHistory))
        let historyHint = NSTextField(labelWithString: "Последние 30 диктовок; двойной щелчок тоже копирует")
        historyHint.font = .systemFont(ofSize: 11)
        historyHint.textColor = .tertiaryLabelColor
        let historyBar = NSStackView(views: [copyButton, historyHint])
        historyBar.spacing = 10

        let test = NSButton(title: "Проверить: начать запись", target: self, action: #selector(testRecording))
        let logButton = NSButton(title: "Показать лог", target: self, action: #selector(openLog))
        let cfgButton = NSButton(title: "config.json", target: self, action: #selector(openConfigFile))
        let bar = NSStackView(views: [test, logButton, cfgButton])
        bar.spacing = 8

        let hint = NSTextField(wrappingLabelWithString:
            "Сочетание — запись, ещё раз — текст появляется в активном поле, Esc — отмена. Изменения применяются сразу.")
        hint.font = .systemFont(ofSize: 11)
        hint.textColor = .tertiaryLabelColor
        hint.preferredMaxLayoutWidth = 512

        let root = NSStackView(views: [header, separator(), form, separator(), section("Последние диктовки"), scroll, historyBar,
                                       separator(), section("Разрешения"), perms, separator(), bar, hint])
        root.orientation = .vertical
        root.alignment = .leading
        root.spacing = 14
        root.edgeInsets = NSEdgeInsets(top: 20, left: 24, bottom: 20, right: 24)
        return root
    }

    // MARK: Состояние

    func refresh() {
        updating = true
        defer { updating = false }
        let c = app.config
        hotkeySpecs = hotkeyPresets
        if !hotkeySpecs.contains(where: { $0.lowercased() == c.hotkey.lowercased() }) { hotkeySpecs.insert(c.hotkey, at: 0) }
        hotkeyPopup.removeAllItems()
        for spec in hotkeySpecs { hotkeyPopup.addItem(withTitle: HotKey.parse(spec)?.title ?? spec) }
        hotkeyPopup.selectItem(at: hotkeySpecs.firstIndex { $0.lowercased() == c.hotkey.lowercased() } ?? 0)
        recordPopup.selectItem(at: recordModeNames.firstIndex { $0.0 == c.recordMode } ?? 0)
        stylePopup.selectItem(at: styleNames.firstIndex { $0.0 == c.style } ?? 0)
        loadHistory(force: true)
        if languagesField.currentEditor() == nil { languagesField.stringValue = c.languages }
        if modelField.currentEditor() == nil { modelField.stringValue = c.model }
        termPopup.selectItem(at: newlineNames.firstIndex { $0.0 == c.newlineInTerminals } ?? 0)
        otherPopup.selectItem(at: newlineNames.firstIndex { $0.0 == c.newlineElsewhere } ?? 1)
        trailingBox.state = c.trailingSpace ? .on : .off
        let installed = FileManager.default.fileExists(atPath: launchAgentPlist)
        autostartBox.isEnabled = installed
        autostartBox.state = installed && !launchAgentDisabled() ? .on : .off
        autostartBox.toolTip = installed ? nil : "Служба не установлена — запусти install-macos.sh"
        refreshStatus()
    }

    func refreshStatus() {
        let micOK = AVCaptureDevice.authorizationStatus(for: .audio) == .authorized
        let axOK = AXIsProcessTrusted()
        micLabel.stringValue = micOK ? "✓ разрешён" : "✗ нет доступа"
        micLabel.textColor = micOK ? .systemGreen : .systemOrange
        axLabel.stringValue = axOK ? "✓ разрешён" : "✗ нет — клавиша не работает"
        axLabel.textColor = axOK ? .systemGreen : .systemOrange
        status.stringValue = app.statusText()
        loadHistory(force: false)
    }

    // MARK: История

    private func loadHistory(force: Bool) {
        let path = homeDir + "/history.json"
        let stamp = (try? FileManager.default.attributesOfItem(atPath: path))?[.modificationDate] as? Date
        if !force, stamp == historyStamp { return }
        historyStamp = stamp
        var items: [(time: String, text: String)] = []
        if let data = FileManager.default.contents(atPath: path),
           let list = (try? JSONSerialization.jsonObject(with: data)) as? [[String: Any]] {
            for entry in list.reversed() {
                guard let text = entry["text"] as? String, !text.isEmpty else { continue }
                items.append((time: entry["time"] as? String ?? "", text: text))
            }
        }
        historyItems = items
        historyTable.reloadData()
    }

    func numberOfRows(in tableView: NSTableView) -> Int { historyItems.count }

    func tableView(_ tableView: NSTableView, objectValueFor tableColumn: NSTableColumn?, row: Int) -> Any? {
        guard row < historyItems.count else { return nil }
        let item = historyItems[row]
        let clock = item.time.count >= 16 ? String(item.time.dropFirst(11).prefix(5)) : ""
        let oneLine = item.text.replacingOccurrences(of: "\n", with: " ")
        return "\(clock)   \(oneLine.prefix(90))"
    }

    @objc private func copyHistory() {
        let row = historyTable.selectedRow
        guard row >= 0, row < historyItems.count else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(historyItems[row].text, forType: .string)
        status.stringValue = "Скопировано в буфер обмена"
    }

    // MARK: Действия

    @objc private func hotkeyChanged() {
        let i = hotkeyPopup.indexOfSelectedItem
        guard !updating, i >= 0, i < hotkeySpecs.count else { return }
        app.apply(["hotkey": hotkeySpecs[i]])
    }

    @objc private func captureHotkey() { app.captureHotkey() }

    @objc private func recordModeChanged() {
        guard !updating, let name = recordPopup.selectedItem?.representedObject as? String else { return }
        app.apply(["record_mode": name])
    }

    @objc private func styleChanged() {
        guard !updating, let name = stylePopup.selectedItem?.representedObject as? String else { return }
        app.apply(["style": name])
    }

    @objc private func previewStyle() { app.previewHUD() }

    @objc private func fieldChanged() { commitFields() }

    func controlTextDidEndEditing(_ obj: Notification) { commitFields() }

    private func commitFields() {
        guard !updating else { return }
        var updates: [String: Any] = [:]
        let langs = languagesField.stringValue.trimmingCharacters(in: .whitespaces)
        let model = modelField.stringValue.trimmingCharacters(in: .whitespaces)
        if !langs.isEmpty, langs != app.config.languages { updates["languages"] = langs }
        if !model.isEmpty, model != app.config.model { updates["model"] = model }
        if !updates.isEmpty { app.apply(updates) }
    }

    @objc private func newlineChanged(_ sender: NSPopUpButton) {
        guard !updating, let name = sender.selectedItem?.representedObject as? String else { return }
        app.apply([sender === termPopup ? "newline_in_terminals" : "newline_elsewhere": name])
    }

    @objc private func trailingChanged() {
        guard !updating else { return }
        app.apply(["trailing_space": trailingBox.state == .on])
    }

    /// Выключено — служба доживает до выхода из системы и больше не стартует; включено — стартует при входе
    /// (и прямо сейчас, если не запущена: тогда она попросит этот ручной экземпляр выйти и откроет своё окно).
    @objc private func autostartChanged() {
        guard !updating else { return }
        let on = autostartBox.state == .on
        let target = "gui/\(getuid())/\(launchdLabel)"
        if on {
            sh("/bin/launchctl", ["enable", target])
            if sh("/bin/launchctl", ["print", target]).0 != 0 {
                sh("/bin/launchctl", ["bootstrap", "gui/\(getuid())", launchAgentPlist])
            }
        } else {
            sh("/bin/launchctl", ["disable", target])
        }
        log("автозапуск при входе: \(on ? "включён" : "выключен")")
        refresh()
    }

    @objc private func openMic() {
        NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")!)
    }

    @objc private func openAX() {
        NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")!)
    }

    @objc private func testRecording() { app.toggle() }

    @objc private func openLog() { NSWorkspace.shared.open(URL(fileURLWithPath: logPath)) }

    @objc private func openConfigFile() { app.openConfig() }
}
