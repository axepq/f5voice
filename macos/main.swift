// F5Voice — локальная диктовка по горячей клавише вместо системной (macOS, Apple Silicon).
//
// Как устроено:
//   1. Горячая клавиша берётся из ~/.f5voice/config.json ("hotkey": "F5" по умолчанию).
//      F1–F12 на клавиатурах Apple в медиарежиме шлют не клавишу, а медиакод, поэтому
//      hidutil переводит и клавишу, и её медиакод в F17, которую никто не использует.
//      Сочетания с модификаторами (cmd+shift+space) и F13–F20 ловятся напрямую.
//   2. Перехват клавиш (CGEventTap): первое нажатие — запись, второе — стоп и
//      распознавание. Esc — отмена записи или распознавания.
//   3. Звук пишется в ~/.f5voice/last.wav (16 кГц, моно), путь уходит воркеру
//      worker.py, который держит модель whisper в памяти и отвечает текстом.
//   4. Текст печатается в активное поле как обычный ввод с клавиатуры.
//   5. Окно программы (settings.swift) открывается двойным щелчком по F5Voice.app, из меню
//      в строке состояния и командой open -a F5Voice; служба запускается с --service без окна.
//
// Сборка: macos/build.sh   Настройки: ~/.f5voice/config.json   Лог: ~/.f5voice/f5voice.log

import AVFoundation
import Cocoa
import Symbols

// MARK: - Пути и настройки

let homeDir = ProcessInfo.processInfo.environment["F5VOICE_HOME"] ?? NSHomeDirectory() + "/.f5voice"
let configPath = homeDir + "/config.json"
let workerScript = homeDir + "/src/macos/worker.py"
let python = homeDir + "/venv/bin/python"
let lastWav = homeDir + "/last.wav"
let logPath = homeDir + "/f5voice.log"
let launchdLabel = "com.alex.f5voice"
let cancelCode: Int64 = 53                 // Esc
let spareKeyCode: CGKeyCode = 64           // F17: сюда hidutil переводит выбранную F-клавишу
let typeQueue = DispatchQueue(label: "f5voice.type", qos: .userInteractive)
/// Модели распознавания речи для окна настроек: (репозиторий, подпись).
let whisperModels = [
    ("mlx-community/whisper-large-v3-turbo", "large-v3-turbo — быстрая (по умолчанию)"),
    ("mlx-community/whisper-large-v3-mlx", "large-v3 — точнее, но медленнее"),
]
/// Модели для переписывания и ответов: (репозиторий, подпись в настройках).
let rewriteModels = [
    ("mlx-community/Qwen3-4B-Instruct-2507-4bit", "Qwen3 4B — быстрая, 3 ГБ (переписывание)"),
    ("mlx-community/Qwen3-8B-4bit", "Qwen3 8B — точнее, 5 ГБ"),
    ("mlx-community/Mistral-Nemo-Instruct-2407-4bit", "Mistral Nemo 12B — живая и вольная, 7 ГБ (ответы)"),
]
/// Встроенные стили переписывания для окна настроек (фраза, что делает). Дублирует BUILTIN_STYLES в common/rewrite.py.
let builtinStyleList: [(phrase: String, summary: String)] = [
    ("официальный стиль", "деловое письмо, обращение на Вы"),
    ("короче", "вдвое короче, главное сохранить"),
    ("исправь ошибки", "только орфография и пунктуация"),
    ("по-английски", "перевод на английский"),
    ("улучши подачу", "яснее донести мысль, без воды"),
    ("технический стиль", "IT-термины, точные формулировки"),
    ("по сути", "кратко и ясно, только смысл без воды"),
    ("команда: своя инструкция", "например «команда: сделай списком»"),
]
let spareKeyUsage: UInt64 = 0x70000006C    // F17 на странице клавиатуры
let transcribeTimeoutSeconds: Double = 30  // дольше — воркер считается зависшим и перезапускается
let rewriteTimeoutSeconds: Double = 120     // переписывание моделью: загрузка + генерация длинного текста
let loadTimeoutSeconds: Double = 600        // воркер грузит (или качает) модель Whisper до отправки запроса
let isService = CommandLine.arguments.contains("--service")  // запущены службой launchd, а не вручную
let openNote = Notification.Name("com.alex.f5voice.open")       // «покажи окно» от повторного запуска
let toggleNote = Notification.Name("com.alex.f5voice.toggle")   // F5Voice --toggle: начать/закончить запись
let cancelNote = Notification.Name("com.alex.f5voice.cancel")   // F5Voice --cancel
let quitNote = Notification.Name("com.alex.f5voice.quit")       // «уступи место службе» ручному экземпляру
var tookOver = false                                            // служба заменила ручной экземпляр

struct Config {
    var hotkey = "F5"
    var languages = "ru,en"
    var altLanguageMinProb = 0.95
    var model = "mlx-community/whisper-large-v3-turbo"
    var prompt = ""
    var trailingSpace = true
    var maxRecordSeconds = 180.0
    var idleUnloadMinutes = 15.0
    var newlineInTerminals = "option"   // option | shift | none
    var newlineElsewhere = "shift"
    var style = "glass"                 // glass | metal | clear | dark
    var recordMode = "auto"             // auto — нажатие или удержание | toggle — только нажатие | hold — только удержание
    // Переписывание по команде и ответы на вопросы: читает воркер из config.json сам, здесь — для окна настроек.
    var rewriteModel = rewriteModels[0].0   // "" — выключено
    var rewriteIdleMinutes = 1.0
    var rewriteKeyword = "команда"
    var rewriteCommands: [(triggers: String, instruction: String)] = []  // свои стили, порядок как в файле
    var answerModel = ""    // ответы выключены по умолчанию; включаются галочкой (тогда берётся Nemo)
    var answerKeyword = "ответь"
    var answerThinking = false
    var loadError: String?

    /// Дописывает ключи в config.json, остальное сохраняя как есть.
    static func save(_ updates: [String: Any]) {
        var obj: [String: Any] = [:]
        if let data = FileManager.default.contents(atPath: configPath),
           let parsed = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] {
            obj = parsed
        }
        for (k, v) in updates { obj[k] = v }
        if let data = try? JSONSerialization.data(withJSONObject: obj, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: URL(fileURLWithPath: configPath))
        }
    }

    static func load() -> Config {
        var c = Config()
        guard let data = FileManager.default.contents(atPath: configPath) else {
            log("настроек нет (\(configPath)) — работаю по умолчанию")
            return c
        }
        let obj: [String: Any]
        do {
            guard let parsed = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                throw NSError(domain: "F5Voice", code: 2, userInfo: [NSLocalizedDescriptionKey: "в корне должен быть объект {…}"])
            }
            obj = parsed
        } catch {
            c.loadError = error.localizedDescription
            log("config.json не разобран: \(error.localizedDescription) — работаю по умолчанию")
            return c
        }
        if let v = obj["hotkey"] as? String, !v.isEmpty { c.hotkey = v }
        if let v = obj["languages"] as? String, !v.isEmpty { c.languages = v }
        if let v = obj["alt_language_min_prob"] as? Double { c.altLanguageMinProb = v }
        if let v = obj["model"] as? String, !v.isEmpty { c.model = v }
        if let v = obj["prompt"] as? String { c.prompt = v }
        if let v = obj["trailing_space"] as? Bool { c.trailingSpace = v }
        if let v = obj["max_record_seconds"] as? Double { c.maxRecordSeconds = v }
        if let v = obj["idle_unload_minutes"] as? Double { c.idleUnloadMinutes = v }
        if let v = obj["newline_in_terminals"] as? String { c.newlineInTerminals = v }
        if let v = obj["newline_elsewhere"] as? String { c.newlineElsewhere = v }
        if let v = obj["style"] as? String, !v.isEmpty { c.style = v }
        if let v = obj["record_mode"] as? String, ["auto", "toggle", "hold"].contains(v) { c.recordMode = v }
        if let v = obj["rewrite_model"] as? String { c.rewriteModel = v }
        if let v = obj["rewrite_idle_minutes"] as? Double { c.rewriteIdleMinutes = v }
        if let v = obj["rewrite_keyword"] as? String { c.rewriteKeyword = v }
        if let v = obj["rewrite_commands"] as? [String: String] {
            c.rewriteCommands = v.keys.sorted().map { (triggers: $0, instruction: v[$0]!) }
        }
        if let v = obj["answer_model"] as? String { c.answerModel = v }
        if let v = obj["answer_keyword"] as? String { c.answerKeyword = v }
        if let v = obj["answer_thinking"] as? Bool { c.answerThinking = v }
        return c
    }

    /// Всё, что требует перезапуска воркера.
    var workerSignature: String { "\(languages)|\(altLanguageMinProb)|\(model)|\(prompt)|\(idleUnloadMinutes)" }
}

// MARK: - Горячая клавиша

struct HotKey {
    var keyCode: CGKeyCode
    var flags: CGEventFlags
    var fKey: Int?          // номер F-клавиши 1…12, если её надо переводить через hidutil
    var title: String

    static let modifierMask: CGEventFlags = [.maskCommand, .maskShift, .maskAlternate, .maskControl]

    static let keyCodes: [String: CGKeyCode] = [
        "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100,
        "f9": 101, "f10": 109, "f11": 103, "f12": 111, "f13": 105, "f14": 107, "f15": 113,
        "f16": 106, "f17": 64, "f18": 79, "f19": 80, "f20": 90,
        "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11,
        "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21,
        "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31,
        "u": 32, "[": 33, "i": 34, "p": 35, "return": 36, "enter": 36, "l": 37, "j": 38, "'": 39,
        "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "tab": 48,
        "space": 49, "`": 50, "delete": 51, "backspace": 51, "escape": 53, "esc": 53,
        "home": 115, "pageup": 116, "forwarddelete": 117, "end": 119, "pagedown": 121,
        "left": 123, "right": 124, "down": 125, "up": 126,
    ]

    /// "F5", "cmd+shift+space", "ctrl+alt+d", "F13"
    static func parse(_ spec: String) -> HotKey? {
        var flags: CGEventFlags = []
        var key: String?
        for raw in spec.lowercased().split(separator: "+") {
            let part = raw.trimmingCharacters(in: .whitespaces)
            switch part {
            case "cmd", "command", "⌘": flags.insert(.maskCommand)
            case "shift", "⇧": flags.insert(.maskShift)
            case "alt", "option", "opt", "⌥": flags.insert(.maskAlternate)
            case "ctrl", "control", "⌃": flags.insert(.maskControl)
            default: key = part
            }
        }
        guard let k = key, let code = keyCodes[k] else { return nil }
        let isFunctionKey = k.hasPrefix("f") && Int(k.dropFirst()) != nil
        if flags.isEmpty && !isFunctionKey { return nil }  // «d» или «space» без модификаторов сломали бы набор текста
        var fKey: Int?
        if isFunctionKey, let n = Int(k.dropFirst()), (1...12).contains(n) { fKey = n }
        let mods = [(CGEventFlags.maskControl, "⌃"), (.maskAlternate, "⌥"), (.maskShift, "⇧"), (.maskCommand, "⌘")]
            .filter { flags.contains($0.0) }.map { $0.1 }.joined()
        return HotKey(keyCode: code, flags: flags, fKey: fKey, title: mods + k.uppercased())
    }

    /// Код, который реально приходит в перехват: F-клавиши 1…12 переведены в F17.
    var effectiveKeyCode: CGKeyCode { fKey != nil ? spareKeyCode : keyCode }

    func matches(_ event: CGEvent) -> Bool {
        guard event.getIntegerValueField(.keyboardEventKeycode) == Int64(effectiveKeyCode) else { return false }
        return event.flags.intersection(HotKey.modifierMask) == flags
    }
}

// MARK: - Служебное

let logFormatter: DateFormatter = {
    let f = DateFormatter()
    f.dateFormat = "yyyy-MM-dd HH:mm:ss"
    return f
}()

func log(_ s: String) {
    let line = "\(logFormatter.string(from: Date())) \(s)\n"
    FileHandle.standardError.write(line.data(using: .utf8)!)
}

@discardableResult
func sh(_ path: String, _ args: [String]) -> (Int32, String) {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: path)
    p.arguments = args
    let pipe = Pipe()
    p.standardOutput = pipe
    p.standardError = pipe
    do { try p.run() } catch { return (-1, "\(error)") }
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    p.waitUntilExit()
    return (p.terminationStatus, String(data: data, encoding: .utf8) ?? "")
}

var mediaUsageCache: [Int: UInt64?] = [:]

/// Медиакод, который клавиатура Apple шлёт вместо F-клавиши в медиарежиме
/// (из FnFunctionUsageMap драйвера), в кодировке hidutil: страница << 32 | код.
func mediaUsage(forFKey n: Int) -> UInt64? {
    if let cached = mediaUsageCache[n] { return cached }
    let found = lookupMediaUsage(forFKey: n)
    mediaUsageCache[n] = found
    return found
}

func lookupMediaUsage(forFKey n: Int) -> UInt64? {
    let (code, out) = sh("/usr/sbin/ioreg", ["-l", "-w0"])
    guard code == 0,
          let range = out.range(of: #""FnFunctionUsageMap" = "([^"]*)""#, options: .regularExpression)
    else { return nil }
    let map = String(out[range]).components(separatedBy: "\"")[3]
    let tokens = map.split(separator: ",").compactMap { UInt64($0.trimmingCharacters(in: .whitespaces).dropFirst(2), radix: 16) }
    let keyboardUsage = UInt64(0x3A + n - 1)  // F1 = 0x3A … F12 = 0x45
    var i = 0
    while i + 1 < tokens.count {
        let src = tokens[i], dst = tokens[i + 1]
        if src >> 16 == 0x0007, src & 0xFFFF == keyboardUsage {
            return ((dst >> 16) << 32) | (dst & 0xFFFF)
        }
        i += 2
    }
    return nil
}

/// Переводит выбранную F-клавишу (и её медиакод) в F17 или снимает перевод.
func applyRemap(fKey: Int?, on: Bool) {
    var mapping = "[]"
    if on, let n = fKey {
        var sources = [UInt64(0x700000000) | UInt64(0x3A + n - 1)]
        if let media = mediaUsage(forFKey: n) {
            sources.append(media)
        } else if n == 5 {
            sources.append(0xC000000CF)  // Voice Command — известный код клавиши диктовки
        }
        mapping = "[" + sources.map {
            String(format: "{\"HIDKeyboardModifierMappingSrc\":0x%llX,\"HIDKeyboardModifierMappingDst\":0x%llX}", $0, spareKeyUsage)
        }.joined(separator: ",") + "]"
    }
    for attempt in 1...5 {
        let (code, out) = sh("/usr/bin/hidutil", ["property", "--set", "{\"UserKeyMapping\":\(mapping)}"])
        if code == 0 {
            log(on && fKey != nil ? "hidutil: F\(fKey!) переведена в F17" : "hidutil: перевод клавиш снят")
            return
        }
        log("hidutil, попытка \(attempt): код \(code) \(out.trimmingCharacters(in: .whitespacesAndNewlines))")
        sleep(3)
    }
}

func play(_ name: String) {
    NSSound(named: NSSound.Name(name))?.play()
}

// MARK: - Печать текста

let terminalBundleIDs: Set<String> = [
    "com.apple.Terminal", "com.googlecode.iterm2", "com.mitchellh.ghostty",
    "dev.warp.Warp-Stable", "io.alacritty", "net.kovidgoyal.kitty", "com.github.wez.wezterm",
]

func newlineFlags(_ config: Config) -> CGEventFlags {
    let front = NSWorkspace.shared.frontmostApplication?.bundleIdentifier ?? ""
    let mode = terminalBundleIDs.contains(front) ? config.newlineInTerminals : config.newlineElsewhere
    switch mode {
    case "option", "alt": return .maskAlternate
    case "shift": return .maskShift
    default: return []
    }
}

func postKey(_ src: CGEventSource, _ keyCode: CGKeyCode, flags: CGEventFlags) {
    guard let down = CGEvent(keyboardEventSource: src, virtualKey: keyCode, keyDown: true),
          let up = CGEvent(keyboardEventSource: src, virtualKey: keyCode, keyDown: false)
    else { return }
    down.flags = flags
    up.flags = flags
    down.post(tap: .cgSessionEventTap)
    up.post(tap: .cgSessionEventTap)
}

/// Печатает текст в активное поле как ввод с клавиатуры (раскладка не важна).
func typeText(_ text: String, config: Config) {
    guard let src = CGEventSource(stateID: .combinedSessionState) else {
        log("не удалось создать источник событий")
        return
    }
    let newline = newlineFlags(config)
    for ch in text {
        if ch == "\n" {
            postKey(src, 36, flags: newline)   // Return с модификатором — перенос без отправки
            usleep(20000)
            continue
        }
        var units = Array(String(ch).utf16)
        guard let down = CGEvent(keyboardEventSource: src, virtualKey: 0, keyDown: true),
              let up = CGEvent(keyboardEventSource: src, virtualKey: 0, keyDown: false)
        else { continue }
        down.flags = []
        up.flags = []
        down.keyboardSetUnicodeString(stringLength: units.count, unicodeString: &units)
        up.keyboardSetUnicodeString(stringLength: units.count, unicodeString: &units)
        down.post(tap: .cgSessionEventTap)
        up.post(tap: .cgSessionEventTap)
        usleep(2000)
    }
}

// MARK: - Запись

final class Recorder {
    private var rec: AVAudioRecorder?

    func start(to path: String) throws {
        try? FileManager.default.removeItem(atPath: path)
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatLinearPCM),
            AVSampleRateKey: 16000.0,
            AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsBigEndianKey: false,
        ]
        let r = try AVAudioRecorder(url: URL(fileURLWithPath: path), settings: settings)
        r.isMeteringEnabled = true
        r.prepareToRecord()
        guard r.record() else {
            throw NSError(domain: "F5Voice", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "record() вернул false"])
        }
        rec = r
    }

    /// Останавливает запись, возвращает её длительность в секундах.
    func stop() -> Double {
        guard let r = rec else { return 0 }
        let t = r.currentTime
        r.stop()
        rec = nil
        return t
    }

    /// Уровень сигнала 0…1 для индикатора.
    func level() -> Float {
        guard let r = rec else { return 0 }
        r.updateMeters()
        let db = r.averagePower(forChannel: 0)          // -160…0
        return max(0, min(1, (db + 55) / 55))
    }
}

// MARK: - Воркер распознавания

final class Worker {
    struct Reply {
        var text = ""
        var error: String?
        var reason: String?
        var sec: Double = 0
        var lang = ""
        var scores = ""
        var rewrite: String?        // ключ команды переписывания
        var rewriteError: String?   // переписать не вышло — text тогда исходный без команды
        var question: String?       // фраза начиналась с «ответь»: text пустой, ответ в answer
        var answer: String?
        var answerError: String?
    }

    var config: Config
    var onReady: (() -> Void)?
    var onStatus: ((String, String) -> Void)?   // (status, command) — промежуточная строка воркера
    var onSent: (() -> Void)?                     // запрос реально ушёл воркеру: с этого момента считаем таймаут
    private(set) var lastExitCode: Int32 = 0      // как завершился прошлый воркер: 0 — штатно (простой)
    private var process: Process?
    private var stdinPipe: Pipe?
    private var buffer = Data()
    private(set) var isReady = false
    private var pending: (path: String, sent: Bool, completion: (Reply) -> Void)?

    init(config: Config) { self.config = config }

    var isRunning: Bool { process?.isRunning ?? false }

    func start() {
        if isRunning { return }
        isReady = false
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = ["-u", workerScript]
        var env = ProcessInfo.processInfo.environment
        env["F5_MODEL"] = config.model
        env["F5_LANGS"] = config.languages
        env["F5_ALT_MIN_PROB"] = String(config.altLanguageMinProb)
        env["F5_PROMPT"] = config.prompt
        env["F5_IDLE_SEC"] = String(Int(config.idleUnloadMinutes * 60))
        env["PYTHONUNBUFFERED"] = "1"
        p.environment = env
        let inPipe = Pipe()
        let outPipe = Pipe()
        p.standardInput = inPipe
        p.standardOutput = outPipe
        p.standardError = FileHandle.standardError
        outPipe.fileHandleForReading.readabilityHandler = { [weak self] h in
            let d = h.availableData
            DispatchQueue.main.async { self?.consume(d) }
        }
        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async {
                outPipe.fileHandleForReading.readabilityHandler = nil
                guard let self = self, self.process === proc else { return }
                log("воркер завершился, код \(proc.terminationStatus)")
                self.lastExitCode = proc.terminationStatus
                self.process = nil
                self.stdinPipe = nil
                self.isReady = false
                self.buffer.removeAll()
                if let pend = self.pending {
                    self.pending = nil
                    pend.completion(Reply(error: "воркер завершился, не ответив"))
                }
            }
        }
        do { try p.run() } catch {
            log("не удалось запустить воркер: \(error)")
            lastExitCode = -1
            if let pend = pending {  // иначе приложение навсегда останется в «Распознаю…»
                pending = nil
                pend.completion(Reply(error: "воркер не запускается — смотри лог"))
            }
            return
        }
        process = p
        stdinPipe = inPipe
        log("воркер запущен, pid \(p.processIdentifier), грузит модель \(config.model)")
    }

    /// Останавливает воркер. Если он что-то распознавал, запрос завершается ошибкой `reason`.
    func stop(reason: String = "воркер остановлен") {
        if let pend = pending {  // до проверки процесса: запрос мог ждать воркер, который так и не стартовал
            pending = nil
            pend.completion(Reply(error: reason))
        }
        guard let p = process else { return }
        process = nil
        stdinPipe = nil
        isReady = false
        buffer.removeAll()
        p.terminate()
    }

    func transcribe(_ path: String, completion: @escaping (Reply) -> Void) { send(path, completion: completion) }

    /// Переписать готовый текст стилем `trigger` (кнопка «Проверить» в настройках).
    func rewrite(_ text: String, trigger: String, completion: @escaping (Reply) -> Void) {
        guard let data = try? JSONSerialization.data(withJSONObject: ["rewrite": ["text": text, "trigger": trigger]]),
              let line = String(data: data, encoding: .utf8) else { return completion(Reply(error: "не собрал запрос")) }
        send(line, completion: completion)
    }

    /// Пустая строка воркеру: началась запись, модель переписывания не должна выгрузиться посреди неё.
    func ping() {
        guard isReady, pending == nil, let pipe = stdinPipe else { return }
        try? pipe.fileHandleForWriting.write(contentsOf: "\n".data(using: .utf8)!)
    }

    private func send(_ line: String, completion: @escaping (Reply) -> Void) {
        if let old = pending {
            old.completion(Reply(error: "вытеснено новым запросом"))
        }
        pending = (line, false, completion)
        start()
        flush()
    }

    private func flush() {
        guard isReady, var pend = pending, !pend.sent, let pipe = stdinPipe else { return }
        pend.sent = true
        pending = pend
        do {
            try pipe.fileHandleForWriting.write(contentsOf: (pend.path + "\n").data(using: .utf8)!)
            onSent?()
        } catch {
            log("не смог передать файл воркеру: \(error.localizedDescription)")
            pending = nil
            pend.completion(Reply(error: "воркер не принял запрос"))
        }
    }

    private func consume(_ d: Data) {
        if d.isEmpty { return }
        buffer.append(d)
        while let nl = buffer.firstIndex(of: 0x0A) {
            let line = buffer.subdata(in: buffer.startIndex..<nl)
            buffer.removeSubrange(buffer.startIndex...nl)
            handle(line)
        }
    }

    private func handle(_ line: Data) {
        guard let obj = (try? JSONSerialization.jsonObject(with: line)) as? [String: Any] else {
            log("воркер прислал не JSON: \(String(data: line, encoding: .utf8) ?? "?")")
            return
        }
        if obj["ready"] as? Bool == true {
            isReady = true
            log("модель загружена за \(obj["load_sec"] ?? 0) с")
            flush()
            onReady?()
            return
        }
        if obj["bye"] != nil { return }
        if let st = obj["status"] as? String {
            onStatus?(st, (obj["command"] as? String) ?? "")
            return
        }
        var r = Reply()
        r.text = (obj["text"] as? String) ?? ""
        r.error = obj["error"] as? String
        r.reason = obj["reason"] as? String
        r.rewrite = obj["rewrite"] as? String
        r.rewriteError = obj["rewrite_error"] as? String
        r.question = obj["question"] as? String
        r.answer = obj["answer"] as? String
        r.answerError = obj["answer_error"] as? String
        r.sec = (obj["sec"] as? Double) ?? 0
        r.lang = (obj["lang"] as? String) ?? ""
        if let fixed = obj["fixed"] as? Int, fixed > 0 { r.lang += ", исправлено сегментов: \(fixed)" }
        if let sc = obj["scores"] as? [String: Double] {
            r.scores = sc.keys.sorted().map { "\($0) \(String(format: "%.2f", sc[$0]!))" }.joined(separator: " / ")
        }
        if let pend = pending {
            pending = nil
            pend.completion(r)
        }
    }
}

// MARK: - Плашка на экране

enum HUDBars { case none, live, wave }

/// Хромовое кольцо по краю плашки: неподвижная сталь (светлее сверху, темнее снизу) и одна мягкая
/// полоска света, которая равномерно обходит контур по кругу (strokeStart/strokeEnd по удвоенному
/// пути — так она не рвётся на стыке). Полоска без жёстких краёв: много полупрозрачных штрихов
/// разной длины с общей вершиной; где их накладывается больше, ярче, к концам плавно гаснет.
final class MetalRing {
    let layer = CALayer()
    private let base = CAGradientLayer()
    private let mask = CAShapeLayer()
    private var glints: [CAShapeLayer] = []
    let width: CGFloat = 3
    private let loopSeconds = 5.0
    private let steps = 16                 // штрихов в полоске
    private let tailLength: CGFloat = 0.22 // доля контура позади вершины
    private let frontLength: CGFloat = 0.07 // впереди вершины

    init() {
        base.type = .axial
        base.startPoint = CGPoint(x: 0.5, y: 1)
        base.endPoint = CGPoint(x: 0.5, y: 0)
        base.colors = [NSColor(calibratedWhite: 0.74, alpha: 1).cgColor, NSColor(calibratedWhite: 0.52, alpha: 1).cgColor,
                       NSColor(calibratedWhite: 0.24, alpha: 1).cgColor, NSColor(calibratedWhite: 0.40, alpha: 1).cgColor]
        base.locations = [0, 0.32, 0.8, 1]
        mask.fillColor = nil
        mask.strokeColor = NSColor.black.cgColor
        mask.lineWidth = width
        layer.mask = mask
        layer.addSublayer(base)
        // Доли считаются по удвоенному пути: один оборот = 0.5. Вершина у всех штрихов общая,
        // i-й штрих тянется на (i/steps) от полной длины хвоста назад и переда вперёд.
        let peak = tailLength / 2
        for i in 0..<steps {
            let f = CGFloat(i + 1) / CGFloat(steps)
            let g = CAShapeLayer()
            g.fillColor = nil
            g.strokeColor = NSColor.white.withAlphaComponent(0.19).cgColor
            g.lineWidth = width
            g.lineCap = .round
            let start = peak - tailLength * f / 2
            let head = peak + frontLength * f / 2
            g.strokeStart = start
            g.strokeEnd = head
            for (key, from) in [("strokeStart", start), ("strokeEnd", head)] {
                let a = CABasicAnimation(keyPath: key)
                a.fromValue = from
                a.toValue = from + 0.5
                a.duration = loopSeconds
                a.repeatCount = .infinity
                a.timingFunction = CAMediaTimingFunction(name: .linear)
                g.add(a, forKey: key)
            }
            layer.addSublayer(g)
            glints.append(g)
        }
    }

    func layout(in bounds: CGRect, cornerRadius: CGFloat, animated: Bool) {
        let inset = bounds.insetBy(dx: width / 2, dy: width / 2)
        let pill = CGPath(roundedRect: inset, cornerWidth: cornerRadius - width / 2, cornerHeight: cornerRadius - width / 2, transform: nil)
        let doubled = CGMutablePath()
        doubled.addPath(pill)
        doubled.addPath(pill)
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        layer.frame = bounds
        mask.frame = bounds
        base.frame = bounds
        if animated, let from = mask.presentation()?.path ?? mask.path {
            let a = CABasicAnimation(keyPath: "path")
            a.fromValue = from
            a.toValue = pill
            a.duration = hudResizeDuration
            a.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
            mask.add(a, forKey: "path")
        }
        mask.path = pill
        for g in glints {
            g.frame = bounds
            g.path = doubled
        }
        CATransaction.commit()
    }
}

let hudResizeDuration = 0.22

enum HUDAnim { case none, breathe, pulse, variableColor }

final class BarsView: NSView {
    var mode: HUDBars = .none { didSet { needsDisplay = true } }
    private var history = [CGFloat](repeating: 0, count: 11)   // 10 видимых + один въезжает справа
    private var phase: CGFloat = 0
    private var smoothed: CGFloat = 0
    private var lastPush = CACurrentMediaTime()
    private var pushInterval = 0.05
    private var timer: Timer?
    private let barW: CGFloat = 3
    private let gap: CGFloat = 2.5

    func start() {
        stop()
        let t = Timer(timeInterval: 1.0 / 60.0, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            if self.mode == .wave { self.phase += 0.075 }
            self.needsDisplay = true
        }
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    func stop() {
        timer?.invalidate()
        timer = nil
    }

    func reset() {
        history = [CGFloat](repeating: 0, count: history.count)
        phase = 0
        smoothed = 0
        lastPush = CACurrentMediaTime()
    }

    /// Новый отсчёт уровня (20 раз в секунду). Столбики едут влево непрерывно: между отсчётами
    /// сдвиг интерполируется по времени, а сам уровень сглажен, чтобы не прыгал.
    func push(_ level: Float) {
        let now = CACurrentMediaTime()
        pushInterval = max(0.02, min(0.2, now - lastPush))
        lastPush = now
        smoothed += (CGFloat(max(0, min(1, level))) - smoothed) * 0.55
        history.removeFirst()
        history.append(smoothed)
    }

    override func draw(_ dirtyRect: NSRect) {
        let n = history.count - 1
        let pitch = barW + gap
        let totalW = CGFloat(n) * barW + CGFloat(n - 1) * gap
        let x0 = (bounds.width - totalW) / 2
        let minH: CGFloat = 3
        let maxH = bounds.height
        func bar(at x: CGFloat, value: CGFloat, alpha: CGFloat) {
            let h = minH + (maxH - minH) * max(0, min(1, value))
            NSColor.white.withAlphaComponent(max(0, min(1, alpha))).setFill()
            NSBezierPath(roundedRect: NSRect(x: x, y: (bounds.height - h) / 2, width: barW, height: h),
                         xRadius: barW / 2, yRadius: barW / 2).fill()
        }
        switch mode {
        case .live:
            let frac = CGFloat(min(1, (CACurrentMediaTime() - lastPush) / pushInterval))
            for i in 0...n {
                let pos = CGFloat(i) - frac                    // 0 — левый край, n-1 — правый
                var alpha = 0.3 + 0.7 * pos / CGFloat(n - 1)
                if pos < 0 { alpha *= 1 + pos }                // уходящий гаснет
                if pos > CGFloat(n - 1) { alpha *= CGFloat(n) - pos }  // входящий проявляется
                bar(at: x0 + pos * pitch, value: history[i], alpha: alpha)
            }
        case .wave:
            for i in 0..<n {
                let v = 0.5 + 0.5 * sin(phase - CGFloat(i) * 0.65)
                bar(at: x0 + CGFloat(i) * pitch, value: v, alpha: 0.5 + 0.5 * v)
            }
        case .none:
            break
        }
    }
}

final class HUD {
    private let panel: NSPanel
    private let icon = NSImageView()
    private let bars = BarsView()
    private let label = NSTextField(labelWithString: "")
    private var hideWork: DispatchWorkItem?
    private let height: CGFloat = 50
    private var ring: MetalRing?
    private var generation = 0        // растёт на каждом показе: устаревшее скрытие не сработает
    private var hiding = false
    let style: String

    deinit { panel.close() }          // смена стиля создаёт новый HUD: старая панель не должна остаться на экране

    init(style: String) {
        self.style = style
        panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 420, height: 50),
                        styleMask: [.borderless, .nonactivatingPanel],
                        backing: .buffered, defer: false)
        panel.level = .statusBar
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.ignoresMouseEvents = true
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]

        let bounds = panel.contentView!.bounds
        let content = NSView(frame: bounds)
        content.wantsLayer = true
        content.autoresizingMask = [.width, .height]

        icon.symbolConfiguration = NSImage.SymbolConfiguration(pointSize: 17, weight: .semibold)
        icon.imageScaling = .scaleProportionallyUpOrDown
        icon.contentTintColor = .white
        label.textColor = .white
        label.font = .systemFont(ofSize: 15, weight: .medium)
        label.lineBreakMode = .byTruncatingTail
        bars.isHidden = true
        content.addSubview(icon)
        content.addSubview(bars)
        content.addSubview(label)

        // macOS 26: Liquid Glass (класс берём динамически, чтобы собираться и на macOS 14–15).
        // Стили: glass — тёмный оттенок, чтобы белый текст читался и поверх светлых окон;
        // metal — тёмное стекло в хромовом кольце «жидкого металла» (MetalRing);
        // clear — прозрачное стекло без оттенка; dark — почти чёрный.
        let tints: [String: NSColor?] = [
            "glass": NSColor(calibratedRed: 0.09, green: 0.08, blue: 0.12, alpha: 0.55),
            "metal": NSColor(calibratedRed: 0.07, green: 0.07, blue: 0.08, alpha: 0.72),
            "clear": nil,
            "dark": NSColor(calibratedWhite: 0.02, alpha: 0.88),
        ]
        if style == "metal" {
            let r = MetalRing()
            content.layer?.addSublayer(r.layer)
            ring = r
        }
        if style == "clear" { label.textColor = .labelColor; icon.contentTintColor = .labelColor }
        if let glassClass = NSClassFromString("NSGlassEffectView") as? NSView.Type {
            let glass = glassClass.init(frame: bounds)
            glass.setValue(25.0, forKey: "cornerRadius")
            if let tint = tints[style] ?? nil { glass.setValue(tint, forKey: "tintColor") }
            if style == "clear" { glass.setValue(1, forKey: "style") }  // NSGlassEffectView.Style.clear
            glass.setValue(content, forKey: "contentView")
            glass.autoresizingMask = [.width, .height]
            panel.contentView = glass
        } else {
            let fx = NSVisualEffectView(frame: bounds)
            fx.material = .hudWindow
            fx.blendingMode = .behindWindow
            fx.state = .active
            fx.appearance = NSAppearance(named: .darkAqua)
            fx.wantsLayer = true
            fx.layer?.cornerRadius = 25
            fx.layer?.masksToBounds = true
            fx.autoresizingMask = [.width, .height]
            fx.addSubview(content)
            panel.contentView = fx
        }
    }

    func show(_ text: String, symbol: String, tint: NSColor = .white, bars mode: HUDBars = .none,
              animate: HUDAnim = .none, hideAfter: Double? = nil) {
        hideWork?.cancel()
        hideWork = nil
        icon.removeAllSymbolEffects()
        icon.image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil)
        icon.contentTintColor = tint
        switch animate {
        case .breathe:
            #if compiler(>=6.0)
            if #available(macOS 15.0, *) { icon.addSymbolEffect(.breathe) } else { icon.addSymbolEffect(.pulse) }
            #else
            icon.addSymbolEffect(.pulse)
            #endif
        case .pulse: icon.addSymbolEffect(.pulse)
        case .variableColor: icon.addSymbolEffect(.variableColor.iterative.reversing)
        case .none: break
        }
        bars.mode = mode
        if mode == .none {
            bars.stop()
            bars.isHidden = true
        } else {
            bars.reset()
            bars.isHidden = false
            bars.start()
        }
        setText(text)
        present()
        if let t = hideAfter {
            let w = DispatchWorkItem { [weak self] in self?.hide() }
            hideWork = w
            DispatchQueue.main.asyncAfter(deadline: .now() + t, execute: w)
        }
    }

    func setText(_ text: String) {
        label.stringValue = text
        layout()
    }

    /// Плашка «материализуется»: из прозрачной и чуть ниже — в полную, за 0,3 с с замедлением к концу.
    private func present() {
        generation += 1
        if panel.isVisible && !hiding { return }
        hiding = false
        let target = panel.frame
        panel.alphaValue = 0
        panel.setFrame(target.offsetBy(dx: 0, dy: -10), display: false)
        panel.orderFrontRegardless()
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = 0.3
            ctx.timingFunction = CAMediaTimingFunction(controlPoints: 0.2, 0.9, 0.3, 1)
            panel.animator().alphaValue = 1
            panel.animator().setFrame(target, display: true)
        }
    }

    func push(level: Float) {
        bars.push(level)
    }

    private func layout() {
        label.sizeToFit()
        let pad: CGFloat = 18
        let iconW: CGFloat = 22
        let barsW: CGFloat = 54
        let gap: CGFloat = 10
        var x = pad
        icon.frame = NSRect(x: x, y: (height - iconW) / 2, width: iconW, height: iconW)
        x += iconW + gap
        if !bars.isHidden {
            bars.frame = NSRect(x: x, y: (height - 22) / 2, width: barsW, height: 22)
            x += barsW + gap
        }
        let labelW = min(label.frame.width, 620)
        label.frame = NSRect(x: x, y: (height - label.frame.height) / 2, width: labelW, height: label.frame.height)
        let w = x + labelW + pad + 2
        guard let screen = NSScreen.main ?? NSScreen.screens.first else { return }
        let vf = screen.visibleFrame
        let target = NSRect(x: vf.midX - w / 2, y: vf.minY + 48, width: w, height: height)
        let animated = panel.isVisible && !hiding && panel.alphaValue > 0.99 && abs(panel.frame.width - w) > 0.5
        if animated {
            NSAnimationContext.runAnimationGroup { ctx in
                ctx.duration = hudResizeDuration
                ctx.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
                panel.animator().setFrame(target, display: true)
            }
        } else {
            panel.setFrame(target, display: true)
        }
        ring?.layout(in: CGRect(x: 0, y: 0, width: w, height: height), cornerRadius: 25, animated: animated)
    }

    /// Уходит за 0,2 с: гаснет и чуть опускается; если за это время снова позвали show — остаётся.
    func hide() {
        hideWork?.cancel()
        hideWork = nil
        guard panel.isVisible, !hiding else { return }
        hiding = true
        generation += 1
        let gen = generation
        bars.stop()
        let frame = panel.frame
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = 0.2
            ctx.timingFunction = CAMediaTimingFunction(name: .easeIn)
            panel.animator().alphaValue = 0
            panel.animator().setFrame(frame.offsetBy(dx: 0, dy: -8), display: true)
        }, completionHandler: { [weak self] in
            guard let self = self, self.generation == gen else { return }
            self.hiding = false
            self.icon.removeAllSymbolEffects()
            self.panel.orderOut(nil)
            self.panel.alphaValue = 1
            self.panel.setFrame(frame, display: false)
        })
    }
}

// MARK: - Приложение

enum State { case idle, recording, transcribing }

/// Режимы записи: auto — нажал и отпустил → переключение, держишь дольше holdAfter → запись до
/// отпускания; toggle — только переключение нажатием; hold — запись только пока держишь.
struct HoldGate {
    enum Action { case start, stop, none }
    var mode = "auto"
    var holdAfter: TimeInterval = 0.35
    private var pressedAt: TimeInterval?
    private var started = false
    var isHeld: Bool { pressedAt != nil }

    mutating func press(recording: Bool, now: TimeInterval = CACurrentMediaTime()) -> Action {
        if pressedAt != nil { return .none }   // автоповтор зажатой клавиши
        pressedAt = now
        started = !recording
        return recording ? .stop : .start
    }

    mutating func release(recording: Bool, now: TimeInterval = CACurrentMediaTime()) -> Action {
        let longHold = pressedAt.map { now - $0 >= holdAfter } ?? false
        let wasStarted = started
        pressedAt = nil
        started = false
        if !(wasStarted && recording) || mode == "toggle" { return .none }
        return (mode == "hold" || longHold) ? .stop : .none
    }
}

final class App: NSObject, NSApplicationDelegate {
    static let shared = App()

    var state: State = .idle
    var tap: CFMachPort?
    private(set) var config = Config.load()
    private(set) var hotkey = HotKey.parse("F5")!
    private var tapFailures = 0
    private var statusItem: NSStatusItem!
    private var hotkeyItem: NSMenuItem!
    private lazy var hud = HUD(style: config.style)
    private lazy var answerPanel = AnswerPanel()
    private var settings: SettingsWindow?
    private var gate = HoldGate()
    private var holdHint: DispatchWorkItem?
    var hotkeyHeld: Bool { gate.isHeld }
    var capturing = false
    private let recorder = Recorder()
    private lazy var worker = Worker(config: config)
    private var accessibilityOK = false
    private var micOK = false
    private var meterTimer: Timer?
    private var stopWork: DispatchWorkItem?
    private var transcribeTimeout: DispatchWorkItem?
    private var signalSources: [DispatchSourceSignal] = []
    private var activity: NSObjectProtocol?

    func applicationDidFinishLaunching(_ notification: Notification) {
        log("F5Voice запущен, pid \(getpid()), каталог \(homeDir)")
        activity = ProcessInfo.processInfo.beginActivity(
            options: .userInitiatedAllowingIdleSystemSleep, reason: "горячая клавиша диктовки")
        applyHotkey(from: config)
        gate.mode = config.recordMode
        setupStatusItem()
        installSignalHandlers()
        applyRemap(fKey: hotkey.fKey, on: true)
        requestMic()
        checkAccessibility()
        worker.start()
        worker.onReady = { [weak self] in
            guard let self = self, self.state == .transcribing else { return }
            self.showTranscribing()
        }
        worker.onSent = { [weak self] in
            guard let self = self, self.state == .transcribing else { return }
            self.armTimeout(transcribeTimeoutSeconds, what: "воркер не ответил")
        }
        worker.onStatus = { [weak self] status, command in
            guard let self = self, self.state == .transcribing else { return }
            let violet = NSColor(calibratedRed: 0.86, green: 0.7, blue: 1.0, alpha: 1)
            switch status {
            case "rewrite":
                self.hud.show("Переписываю: \(command)…   Esc — отменить", symbol: "wand.and.stars", tint: violet,
                              bars: .wave, animate: .pulse)
                self.armTimeout(rewriteTimeoutSeconds, what: "модель переписывания не ответила")
            case "answer":
                self.hud.show("Отвечаю: \(command)…   Esc — отменить", symbol: "bubble.left.and.text.bubble.right",
                              tint: violet, bars: .wave, animate: .pulse)
                self.armTimeout(rewriteTimeoutSeconds * 2, what: "модель не ответила на вопрос")
            case "download":
                let name = command.split(separator: "/").last.map(String.init) ?? command
                self.hud.show("Скачиваю модель \(name), это один раз…   Esc — отменить", symbol: "arrow.down.circle",
                              tint: violet, bars: .wave, animate: .pulse)
                self.armTimeout(3600, what: "модель не скачалась")
            default:
                break
            }
        }
        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didWakeNotification, object: nil, queue: .main
        ) { [weak self] _ in
            guard let self = self else { return }
            applyRemap(fKey: self.hotkey.fKey, on: true)
        }
        let center = DistributedNotificationCenter.default()
        center.addObserver(forName: openNote, object: nil, queue: .main) { [weak self] _ in self?.showSettings() }
        center.addObserver(forName: toggleNote, object: nil, queue: .main) { [weak self] _ in self?.toggle() }
        center.addObserver(forName: cancelNote, object: nil, queue: .main) { [weak self] _ in self?.cancel() }
        center.addObserver(forName: quitNote, object: nil, queue: .main) { [weak self] _ in
            guard !isService else { return }
            log("служба F5Voice запустилась — ручной экземпляр уступает ей место")
            self?.terminate()
        }
        if !isService || tookOver { showSettings() }  // открыли приложение руками — покажем окно
    }

    /// Двойной щелчок по F5Voice.app или open -a F5Voice, когда программа уже работает.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showSettings()
        return false
    }

    @objc func showSettings() {
        if settings == nil { settings = SettingsWindow(app: self) }
        settings?.show()
    }

    func statusText() -> String {
        if !accessibilityOK { return "Нужно разрешение «Универсальный доступ» — кнопка ниже. Без него \(hotkey.title) не работает." }
        if !micOK { return "Нет доступа к микрофону — кнопка ниже." }
        switch state {
        case .recording: return "Запись…   \(hotkey.title) — готово, Esc — отмена"
        case .transcribing: return "Распознаю…"
        case .idle:
            let s = worker.isReady ? "Готов" : (!worker.isRunning && worker.lastExitCode != 0
                                                    ? "Воркер упал (код \(worker.lastExitCode)) — смотри лог" : "Загружаю модель…")
            return s + "   \(hotkey.title) — диктовка, ещё раз — готово, Esc — отмена"
                + "\nМодель \(config.model)"
        }
    }

    private func applyHotkey(from config: Config) {
        if let hk = HotKey.parse(config.hotkey) {
            hotkey = hk
            log("горячая клавиша: \(hk.title)" + (hk.fKey != nil ? " (через перевод в F17)" : ""))
        } else {
            hotkey = HotKey.parse("F5")!
            log("не понял hotkey «\(config.hotkey)» в настройках — использую F5 (нужна F1–F20 или сочетание с cmd/ctrl/alt/shift)")
            hud.show("Не понял hotkey «\(config.hotkey)», работаю с F5", symbol: "exclamationmark.triangle.fill", tint: .systemOrange, hideAfter: 6)
        }
    }

    // MARK: Настройки

    @objc private func reloadConfig() {
        let old = config
        let oldHotkey = hotkey
        config = Config.load()
        if let err = config.loadError {
            hud.show("config.json не разобран: \(err)", symbol: "exclamationmark.triangle.fill", tint: .systemOrange, hideAfter: 6)
            play("Basso")
            config = old
            return
        }
        applyHotkey(from: config)
        if oldHotkey.fKey != hotkey.fKey {
            if oldHotkey.fKey != nil { applyRemap(fKey: nil, on: false) }
            if hotkey.fKey != nil { applyRemap(fKey: hotkey.fKey, on: true) }
        }
        hotkeyItem.title = hotkeyTitle()
        gate.mode = config.recordMode
        if old.style != config.style {
            hud.hide()
            hud = HUD(style: config.style)
        }
        setupStatusItem()
        worker.config = config
        if old.workerSignature != config.workerSignature {
            log("настройки модели изменились — перезапускаю воркер")
            worker.stop(reason: "настройки изменились")
            worker.start()
        }
        settings?.refresh()
        hud.show("Настройки перечитаны: \(hotkey.title)", symbol: "checkmark.circle.fill", tint: .systemGreen, hideAfter: 3)
        log("настройки перечитаны")
    }

    /// Записать ключи в config.json и применить (окно настроек).
    func apply(_ updates: [String: Any]) {
        Config.save(updates)
        reloadConfig()
    }

    /// «Проверить» в настройках: переписать текст стилем `trigger`, результат — в completion (на главном потоке).
    func rewriteSample(_ text: String, trigger: String, completion: @escaping (Worker.Reply) -> Void) {
        guard state == .idle else { return completion(Worker.Reply(error: "сейчас идёт запись или распознавание")) }
        worker.rewrite(text, trigger: trigger, completion: completion)
    }

    func previewHUD() {
        let title = styleNames.first { $0.0 == config.style }?.1 ?? config.style
        hud.show("Так выглядит плашка: \(title)", symbol: "sparkles", bars: .wave, animate: .variableColor, hideAfter: 3)
    }

    @objc func captureHotkey() {
        capturing = true
        hud.show("Нажмите новое сочетание клавиш… Esc — отмена", symbol: "keyboard", tint: .systemYellow)
    }

    /// Вызывается из перехвата: нажата не-модификаторная клавиша в режиме записи сочетания.
    func finishCapture(keyCode: Int64, flags: CGEventFlags) {
        capturing = false
        guard keyCode != cancelCode else { hud.show("Отменено", symbol: "xmark.circle.fill", hideAfter: 1); return }
        var found = HotKey.keyCodes.first(where: { $0.value == CGKeyCode(keyCode) })?.key
        if CGKeyCode(keyCode) == spareKeyCode, let f = hotkey.fKey { found = "f\(f)" }  // это наша же F-клавиша после перевода в F17
        guard let name = found else {
            hud.show("Эту клавишу назначить нельзя", symbol: "exclamationmark.triangle.fill", tint: .systemOrange, hideAfter: 3)
            return
        }
        var parts: [String] = []
        if flags.contains(.maskControl) { parts.append("ctrl") }
        if flags.contains(.maskAlternate) { parts.append("alt") }
        if flags.contains(.maskShift) { parts.append("shift") }
        if flags.contains(.maskCommand) { parts.append("cmd") }
        parts.append(name)
        let spec = parts.joined(separator: "+")
        guard HotKey.parse(spec) != nil else {
            hud.show("Нужна F-клавиша или сочетание с модификатором", symbol: "exclamationmark.triangle.fill", tint: .systemOrange, hideAfter: 4)
            return
        }
        Config.save(["hotkey": spec])
        reloadConfig()
    }

    @objc func openConfig() {
        if !FileManager.default.fileExists(atPath: configPath) {
            let example = homeDir + "/src/macos/config.example.json"
            try? FileManager.default.copyItem(atPath: example, toPath: configPath)
        }
        NSWorkspace.shared.open(URL(fileURLWithPath: configPath))
    }

    // MARK: Разрешения

    private func requestMic() {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            micOK = true
            log("микрофон: доступ есть")
        case .notDetermined:
            log("микрофон: запрашиваю доступ")
            AVCaptureDevice.requestAccess(for: .audio) { ok in
                DispatchQueue.main.async {
                    self.micOK = ok
                    log("микрофон: \(ok ? "доступ дан" : "отказано")")
                    self.updateIcon()
                }
            }
        default:
            micOK = false
            log("микрофон: доступа нет — Системные настройки → Конфиденциальность и безопасность → Микрофон → F5Voice")
        }
    }

    private func checkAccessibility() {
        let opts = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        accessibilityOK = AXIsProcessTrustedWithOptions(opts)
        if accessibilityOK {
            log("универсальный доступ: есть")
            installTap()
        } else {
            log("универсальный доступ: нет — Системные настройки → Конфиденциальность и безопасность → Универсальный доступ → включить F5Voice")
            hud.show("Включите F5Voice в «Универсальный доступ», чтобы \(hotkey.title) заработала",
                     symbol: "exclamationmark.shield.fill", tint: .systemOrange, hideAfter: 12)
        }
        updateIcon()
        Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] t in
            guard let self = self else { t.invalidate(); return }
            if self.tap != nil { t.invalidate(); return }
            if AXIsProcessTrusted() {
                if !self.accessibilityOK {
                    self.accessibilityOK = true
                    log("универсальный доступ: дали")
                }
                self.installTap()
                self.updateIcon()
            }
        }
    }

    private func installTap() {
        guard tap == nil else { return }
        let mask = CGEventMask(1 << CGEventType.keyDown.rawValue) | CGEventMask(1 << CGEventType.keyUp.rawValue)
        guard let t = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap,
                                        options: .defaultTap, eventsOfInterest: mask,
                                        callback: tapCallback, userInfo: nil)
        else {
            tapFailures += 1
            log("не удалось поставить перехват клавиш (попытка \(tapFailures))")
            if tapFailures >= 3 {
                log("перезапускаюсь, чтобы подхватить разрешение")
                shutdown(removeRemap: false)
                exit(1)
            }
            return
        }
        let src = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, t, 0)
        CFRunLoopAddSource(CFRunLoopGetMain(), src, .commonModes)
        CGEvent.tapEnable(tap: t, enable: true)
        tap = t
        log("перехват \(hotkey.title) активен — можно диктовать")
        hud.show("F5Voice готов: \(hotkey.title) — диктовка", symbol: "checkmark.circle.fill", tint: .systemGreen, hideAfter: 3)
    }

    // MARK: Меню в строке состояния

    private func hotkeyTitle() -> String { "\(hotkey.title) — диктовка, ещё раз — готово, Esc — отмена" }

    private func setupStatusItem() {
        if statusItem == nil { statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength) }
        let menu = NSMenu()
        hotkeyItem = NSMenuItem(title: hotkeyTitle(), action: nil, keyEquivalent: "")
        menu.addItem(hotkeyItem)
        menu.addItem(NSMenuItem(title: "Начать / остановить запись", action: #selector(menuToggle), keyEquivalent: ""))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Настройки F5Voice…", action: #selector(showSettings), keyEquivalent: ","))
        menu.addItem(NSMenuItem(title: "Перечитать config.json", action: #selector(reloadConfig), keyEquivalent: "r"))
        menu.addItem(NSMenuItem(title: "Показать лог", action: #selector(openLog), keyEquivalent: ""))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Выключить до следующего входа (клавиша вернётся системе)", action: #selector(quit), keyEquivalent: "q"))
        for item in menu.items { item.target = self }
        statusItem.menu = menu
        updateIcon()
    }

    private func updateIcon() {
        guard let button = statusItem.button else { return }
        var name = "mic"
        var tint: NSColor?
        var tip = "F5Voice: \(hotkey.title) — диктовка"
        if !accessibilityOK || !micOK {
            name = "mic.slash"
            tip = "F5Voice: не хватает разрешений, смотри меню"
        }
        switch state {
        case .recording:
            name = "mic.fill"
            tint = .systemRed
            tip = "Запись… \(hotkey.title) — готово"
        case .transcribing:
            name = "waveform"
            tint = .systemOrange
            tip = "Распознаю…"
        case .idle:
            break
        }
        let image = NSImage(systemSymbolName: name, accessibilityDescription: tip)
        image?.isTemplate = true
        button.image = image
        button.contentTintColor = tint
        button.toolTip = tip
    }

    @objc private func menuToggle() { toggle() }

    @objc private func openLog() {
        NSWorkspace.shared.open(URL(fileURLWithPath: logPath))
    }

    /// Служба живёт под launchd с KeepAlive: обычный exit() тут же перезапустят.
    /// Поэтому снимаем службу целиком — launchd пришлёт SIGTERM, обработчик
    /// снимет перевод клавиши и выйдет. Если запущены вручную, через 2 с выходим сами.
    @objc func quit() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        p.arguments = ["bootout", "gui/\(getuid())/\(launchdLabel)"]
        try? p.run()
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { self.terminate() }
    }

    func terminate() {
        shutdown(removeRemap: true)
        exit(0)
    }

    private func shutdown(removeRemap: Bool) {
        if state == .recording { _ = recorder.stop() }
        worker.stop()
        if removeRemap, hotkey.fKey != nil { applyRemap(fKey: nil, on: false) }
        log("F5Voice выключен")
    }

    private func installSignalHandlers() {
        signal(SIGPIPE, SIG_IGN)
        for sig in [SIGTERM, SIGINT, SIGHUP] {
            signal(sig, SIG_IGN)
            let src = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            src.setEventHandler { App.shared.terminate() }
            src.resume()
            signalSources.append(src)
        }
    }

    // MARK: Диктовка

    func toggle() {
        switch state {
        case .idle: startRecording()
        case .recording: stopRecording(andTranscribe: true)
        case .transcribing: showTranscribing()
        }
    }

    /// Горячая клавиша нажата (без автоповтора). Время берём из события: старт записи задерживает
    /// главный поток, и «сейчас» при обработке отпускания было бы позже настоящего.
    func hotkeyPressed(at time: TimeInterval) {
        if state == .transcribing { showTranscribing(); return }
        switch gate.press(recording: state == .recording, now: time) {
        case .start:
            startRecording()
            if gate.mode != "toggle" {
                let hint = DispatchWorkItem { [weak self] in
                    guard let self = self, self.gate.isHeld, self.state == .recording else { return }
                    self.hud.setText("Говорите…   отпустите — готово · Esc — отмена")
                }
                holdHint = hint
                DispatchQueue.main.asyncAfter(deadline: .now() + gate.holdAfter, execute: hint)
            }
        case .stop: stopRecording(andTranscribe: true)
        case .none: break
        }
    }

    func hotkeyReleased(at time: TimeInterval) {
        holdHint?.cancel()
        if gate.release(recording: state == .recording, now: time) == .stop { stopRecording(andTranscribe: true) }
    }

    func cancel() {
        switch state {
        case .recording: stopRecording(andTranscribe: false)
        case .transcribing: cancelTranscription()
        case .idle: break
        }
    }

    /// Esc во время распознавания: убиваем воркер (запрос завершится «отменено»)
    /// и сразу поднимаем новый, чтобы следующая диктовка не ждала модель.
    private func cancelTranscription() {
        worker.stop(reason: "отменено")
        worker.start()
    }

    private func showTranscribing() {
        if worker.isReady {
            hud.show("Распознаю…   Esc — отменить", symbol: "waveform", bars: .wave, animate: .variableColor)
        } else {
            hud.show("Загружаю модель…   Esc — отменить", symbol: "brain",
                     tint: NSColor(calibratedRed: 0.86, green: 0.7, blue: 1.0, alpha: 1), bars: .wave, animate: .pulse)
        }
    }

    private func startRecording() {
        if !micOK, AVCaptureDevice.authorizationStatus(for: .audio) == .authorized { micOK = true }
        guard micOK else {
            play("Basso")
            hud.show("Нет доступа к микрофону: Системные настройки → Конфиденциальность → Микрофон → F5Voice",
                     symbol: "mic.slash.fill", tint: .systemOrange, hideAfter: 6)
            return
        }
        worker.ping()
        do {
            try recorder.start(to: lastWav)
        } catch {
            log("запись не началась: \(error.localizedDescription)")
            play("Basso")
            hud.show("Не удалось начать запись — смотри лог", symbol: "exclamationmark.triangle.fill", tint: .systemOrange, hideAfter: 4)
            return
        }
        state = .recording
        worker.start()  // если выгрузился по простою — грузится параллельно с записью
        play("Tink")
        updateIcon()
        hud.show("Говорите…   \(hotkey.title) — готово · Esc — отмена", symbol: "mic.fill", tint: .systemRed, bars: .live, animate: .breathe)
        let meter = Timer(timeInterval: 0.05, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            self.hud.push(level: self.recorder.level())
        }
        RunLoop.main.add(meter, forMode: .common)
        meterTimer = meter
        let work = DispatchWorkItem { [weak self] in self?.stopRecording(andTranscribe: true) }
        stopWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + config.maxRecordSeconds, execute: work)
    }

    private func stopRecording(andTranscribe: Bool) {
        guard state == .recording else { return }
        stopWork?.cancel()
        stopWork = nil
        meterTimer?.invalidate()
        meterTimer = nil
        let seconds = recorder.stop()
        guard andTranscribe else {
            state = .idle
            updateIcon()
            play("Pop")
            hud.show("Отменено", symbol: "xmark.circle.fill", hideAfter: 1)
            log("запись отменена")
            return
        }
        state = .transcribing
        updateIcon()
        play("Pop")
        showTranscribing()
        log(String(format: "записано %.1f с, распознаю", seconds))
        // Пока воркер грузит (или качает) модель, запрос лежит в очереди: на это даём долгий таймаут.
        // Короткий таймаут распознавания взводится в onSent, когда путь реально ушёл воркеру.
        armTimeout(loadTimeoutSeconds, what: "воркер не загрузил модель")
        worker.transcribe(lastWav) { [weak self] reply in
            self?.handle(reply)
        }
    }

    /// Перезапустить воркер, если он не ответил за `seconds`; предыдущий таймер отменяется.
    func armTimeout(_ seconds: Double, what: String) {
        transcribeTimeout?.cancel()
        let timeout = DispatchWorkItem { [weak self] in
            guard let self = self, self.state == .transcribing else { return }
            log("\(what) за \(Int(seconds)) с — перезапускаю его")
            self.worker.stop(reason: "распознавание зависло и было прервано")
            self.worker.start()
        }
        transcribeTimeout = timeout
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds, execute: timeout)
    }

    private func handle(_ reply: Worker.Reply) {
        transcribeTimeout?.cancel()
        transcribeTimeout = nil
        state = .idle
        updateIcon()
        if reply.error == "отменено" {
            log("распознавание отменено")
            play("Pop")
            hud.show("Отменено", symbol: "xmark.circle.fill", hideAfter: 1)
            return
        }
        if let err = reply.error {
            log("ошибка распознавания: \(err)")
            play("Basso")
            hud.show("Ошибка распознавания — смотри лог", symbol: "exclamationmark.triangle.fill", tint: .systemOrange, hideAfter: 4)
            return
        }
        if let q = reply.question {
            if let a = reply.answer {
                log("ответ на «\(q.prefix(60))»: \(a.count) символов")
                hud.hide()
                answerPanel.show(question: q, answer: a)
            } else {
                log("ответить не вышло: \(reply.answerError ?? "?")")
                play("Basso")
                hud.show("Не смог ответить — смотри лог", symbol: "exclamationmark.triangle.fill", tint: .systemOrange, hideAfter: 4)
            }
            return
        }
        if reply.text.isEmpty {
            log("пусто (\(reply.reason ?? "модель ничего не разобрала"))")
            play("Basso")
            hud.show("Ничего не услышал", symbol: "mic.slash.fill", hideAfter: 2)
            return
        }
        if let err = reply.rewriteError {
            log("переписать не вышло (\(reply.rewrite ?? "?")): \(err) — вставляю исходный текст")
            play("Basso")
            hud.show("Не смог переписать, вставляю как сказано", symbol: "exclamationmark.triangle.fill",
                     tint: .systemOrange, hideAfter: 3)
        } else {
            if let key = reply.rewrite { log("переписано (\(key))") }
            hud.hide()
        }
        log(String(format: "готово: %d символов, язык %@ (%@), распознавание %.2f с",
                   reply.text.count, reply.lang, reply.scores, reply.sec))
        let endsWithNewline = reply.text.hasSuffix("\n")
        let text = (config.trailingSpace && !endsWithNewline) ? reply.text + " " : reply.text
        let cfg = config
        typeQueue.async { typeText(text, config: cfg) }  // одна очередь: две диктовки подряд не перемешиваются
    }
}

// MARK: - Перехват клавиш (вызывается на главном runloop)

func tapCallback(proxy: CGEventTapProxy, type: CGEventType, event: CGEvent,
                 refcon: UnsafeMutableRawPointer?) -> Unmanaged<CGEvent>? {
    let app = App.shared
    if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
        if let t = app.tap { CGEvent.tapEnable(tap: t, enable: true) }
        return Unmanaged.passUnretained(event)
    }
    if app.capturing {
        let code = event.getIntegerValueField(.keyboardEventKeycode)
        if (54...63).contains(code) { return Unmanaged.passUnretained(event) }  // сами модификаторы
        if type == .keyDown {
            let flags = event.flags
            DispatchQueue.main.async { app.finishCapture(keyCode: code, flags: flags) }
        }
        return nil
    }
    let when = Double(event.timestamp) / 1_000_000_000   // секунды с загрузки, та же шкала, что CACurrentMediaTime
    if app.hotkey.matches(event) {
        if type == .keyDown, event.getIntegerValueField(.keyboardEventAutorepeat) == 0 {
            DispatchQueue.main.async { app.hotkeyPressed(at: when) }
        } else if type == .keyUp {
            DispatchQueue.main.async { app.hotkeyReleased(at: when) }
        }
        return nil
    }
    let code = event.getIntegerValueField(.keyboardEventKeycode)
    if type == .keyUp, code == Int64(app.hotkey.effectiveKeyCode), app.hotkeyHeld {
        // модификатор отпустили раньше клавиши — отпускание всё равно наше
        DispatchQueue.main.async { app.hotkeyReleased(at: when) }
        return nil
    }
    if code == cancelCode, app.state != .idle {
        if type == .keyDown { DispatchQueue.main.async { app.cancel() } }
        return nil
    }
    return Unmanaged.passUnretained(event)
}

// MARK: - Запуск

if CommandLine.arguments.contains("--check") {
    let c = Config.load()
    print("настройки: \(configPath)" + (c.loadError.map { " — ОШИБКА: \($0)" } ?? ""))
    if let hk = HotKey.parse(c.hotkey) {
        print("hotkey: \(hk.title) → keyCode \(hk.effectiveKeyCode)" + (hk.fKey != nil ? " (F\(hk.fKey!) переводится в F17, медиакод \(mediaUsage(forFKey: hk.fKey!).map { String(format: "0x%llX", $0) } ?? "не найден"))" : ""))
    } else {
        print("hotkey «\(c.hotkey)» НЕ РАЗОБРАН — нужна F1–F20 или сочетание с cmd/ctrl/alt/shift; будет F5")
    }
    print("languages: \(c.languages), alt ≥ \(c.altLanguageMinProb), model: \(c.model)")
    print("prompt: \(c.prompt.isEmpty ? "стандартная" : c.prompt)")
    print("trailing_space: \(c.trailingSpace), max_record_seconds: \(Int(c.maxRecordSeconds)), idle_unload_minutes: \(Int(c.idleUnloadMinutes))")
    print("newline: терминалы — \(c.newlineInTerminals), остальные — \(c.newlineElsewhere)")
    print("воркер: \(workerScript) — \(FileManager.default.fileExists(atPath: workerScript) ? "есть" : "НЕТ")")
    print("python: \(python) — \(FileManager.default.isExecutableFile(atPath: python) ? "есть" : "НЕТ")")
    exit(0)
}

// F5Voice --toggle / --cancel: команда работающему экземпляру (для скриптов, Shortcuts, Raycast).
for (flag, note) in [("--toggle", toggleNote), ("--cancel", cancelNote)] where CommandLine.arguments.contains(flag) {
    let running = !NSRunningApplication.runningApplications(withBundleIdentifier: Bundle.main.bundleIdentifier ?? launchdLabel)
        .filter { $0.processIdentifier != getpid() }.isEmpty
    guard running else { print("F5Voice не запущен"); exit(1) }
    DistributedNotificationCenter.default().postNotificationName(note, object: nil, userInfo: nil, deliverImmediately: true)
    exit(0)
}

// Один экземпляр. Повторный запуск (двойной щелчок по F5Voice.app, open -a F5Voice) открывает окно
// уже работающего; служба (--service), наоборот, вытесняет запущенный вручную экземпляр.
let bundleID = Bundle.main.bundleIdentifier ?? launchdLabel
let others = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).filter { $0.processIdentifier != getpid() }
if !others.isEmpty {
    let center = DistributedNotificationCenter.default()
    if isService {
        center.postNotificationName(quitNote, object: nil, userInfo: nil, deliverImmediately: true)
        var waited = 0
        while waited < 50, others.contains(where: { kill($0.processIdentifier, 0) == 0 }) {
            usleep(100_000)
            waited += 1
        }
        for other in others where kill(other.processIdentifier, 0) == 0 { other.forceTerminate() }
        tookOver = true
        log("служба заменила запущенный вручную экземпляр (pid \(others.map { String($0.processIdentifier) }.joined(separator: ", ")))")
    } else {
        center.postNotificationName(openNote, object: nil, userInfo: nil, deliverImmediately: true)
        exit(0)
    }
}

let application = NSApplication.shared

// MARK: - Окно ответа («ответь, …»)

/// Esc закрывает панель, откуда бы ни пришло событие (текст в панели не редактируется и Esc не глотает).
final class EscPanel: NSPanel {
    override func cancelOperation(_ sender: Any?) { close() }
}

final class AnswerPanel: NSObject {
    private let panel: EscPanel
    private let questionLabel = NSTextField(wrappingLabelWithString: "")
    private let textView = NSTextView()
    private let scroll = NSScrollView()
    private var previousApp: NSRunningApplication?
    private var answer = ""

    override init() {
        panel = EscPanel(contentRect: NSRect(x: 0, y: 0, width: 560, height: 300),
                         styleMask: [.titled, .closable, .resizable, .utilityWindow], backing: .buffered, defer: false)
        super.init()
        panel.title = "Ответ"
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.minSize = NSSize(width: 380, height: 220)
        panel.collectionBehavior = [.moveToActiveSpace, .fullScreenAuxiliary]

        questionLabel.font = .systemFont(ofSize: 12)
        questionLabel.textColor = .secondaryLabelColor
        questionLabel.preferredMaxLayoutWidth = 512

        textView.isEditable = false
        textView.isSelectable = true
        textView.font = .systemFont(ofSize: 14)
        textView.textContainerInset = NSSize(width: 6, height: 8)
        textView.isVerticallyResizable = true
        textView.isHorizontallyResizable = false
        textView.autoresizingMask = [.width]
        textView.textContainer?.widthTracksTextView = true
        scroll.documentView = textView
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        scroll.translatesAutoresizingMaskIntoConstraints = false
        scroll.setContentHuggingPriority(.defaultLow, for: .vertical)

        let copy = NSButton(title: "Скопировать", target: self, action: #selector(copyAnswer))
        let paste = NSButton(title: "Вставить в поле", target: self, action: #selector(pasteAnswer))
        paste.keyEquivalent = "\r"
        let close = NSButton(title: "Закрыть", target: self, action: #selector(closePanel))
        let spacer = NSView()
        spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        let buttons = NSStackView(views: [copy, paste, spacer, close])
        buttons.spacing = 8

        let root = NSStackView(views: [questionLabel, scroll, buttons])
        root.orientation = .vertical
        root.alignment = .leading
        root.spacing = 12
        root.edgeInsets = NSEdgeInsets(top: 16, left: 20, bottom: 16, right: 20)
        root.translatesAutoresizingMaskIntoConstraints = false
        panel.contentView = root
        NSLayoutConstraint.activate([
            scroll.widthAnchor.constraint(equalTo: root.widthAnchor, constant: -40),
            buttons.widthAnchor.constraint(equalTo: root.widthAnchor, constant: -40),
            questionLabel.widthAnchor.constraint(equalTo: root.widthAnchor, constant: -40),
        ])
    }

    func show(question: String, answer: String) {
        self.answer = answer
        previousApp = NSWorkspace.shared.frontmostApplication
        questionLabel.stringValue = question
        textView.string = answer
        // Высота по тексту: от 220 до 60% экрана, дальше прокрутка.
        let screen = NSScreen.screens.first { NSMouseInRect(NSEvent.mouseLocation, $0.frame, false) } ?? NSScreen.main
        let visible = screen?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1200, height: 800)
        let width: CGFloat = 560
        textView.frame.size.width = width - 40 - 12
        textView.layoutManager?.ensureLayout(for: textView.textContainer!)
        let textHeight = (textView.layoutManager?.usedRect(for: textView.textContainer!).height ?? 100) + 16
        let height = min(max(textHeight + 120, 220), visible.height * 0.6)
        let origin = NSPoint(x: visible.midX - width / 2, y: visible.maxY - height - visible.height * 0.12)
        panel.setFrame(NSRect(origin: origin, size: NSSize(width: width, height: height)), display: true)
        textView.scroll(.zero)
        panel.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc private func copyAnswer() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(answer, forType: .string)
    }

    /// Вернуть фокус туда, где был пользователь, и напечатать ответ, как обычную диктовку.
    @objc private func pasteAnswer() {
        let text = answer
        let cfg = App.shared.config
        panel.close()
        previousApp?.activate(options: [])
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) {
            typeQueue.async { typeText(text, config: cfg) }
        }
    }

    @objc private func closePanel() { panel.close() }
}

application.setActivationPolicy(.accessory)
application.delegate = App.shared
application.run()
