// Иконка F5Voice: чёрная стеклянная плитка в сетке macOS, стальная кромка и хромированный
// микрофон в духе «жидкого металла» (диагональные полосы света, резкий горизонт отражения).
// Запуск: swiftc icon.swift -o icon && ./icon out.png   (обёртка: macos/make-icon.sh)
import Cocoa
import CoreImage

let S: CGFloat = 1024
let outPath = CommandLine.arguments[1]

func rgb(_ hex: UInt32, _ a: CGFloat = 1) -> NSColor {
    NSColor(calibratedRed: CGFloat((hex >> 16) & 0xff) / 255, green: CGFloat((hex >> 8) & 0xff) / 255,
            blue: CGFloat(hex & 0xff) / 255, alpha: a)
}

func makeRep(gray: Bool = false) -> NSBitmapImageRep {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(S), pixelsHigh: Int(S),
                               bitsPerSample: 8, samplesPerPixel: gray ? 1 : 4, hasAlpha: !gray,
                               isPlanar: false, colorSpaceName: gray ? .deviceWhite : .deviceRGB,
                               bytesPerRow: 0, bitsPerPixel: 0)!
    rep.size = NSSize(width: S, height: S)
    return rep
}

func draw(into rep: NSBitmapImageRep, _ body: (CGContext) -> Void) {
    NSGraphicsContext.saveGraphicsState()
    let ctx = NSGraphicsContext(bitmapImageRep: rep)!
    NSGraphicsContext.current = ctx
    body(ctx.cgContext)
    NSGraphicsContext.restoreGraphicsState()
}

func radial(_ ctx: CGContext, center: CGPoint, radius: CGFloat, inner: NSColor, outer: NSColor) {
    let g = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(),
                       colors: [inner.cgColor, outer.cgColor] as CFArray, locations: [0, 1])!
    ctx.drawRadialGradient(g, startCenter: center, startRadius: 0, endCenter: center, endRadius: radius,
                           options: [.drawsAfterEndLocation])
}

func linear(_ ctx: CGContext, from: CGPoint, to: CGPoint, colors: [NSColor], locations: [CGFloat]) {
    let g = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(),
                       colors: colors.map { $0.cgColor } as CFArray, locations: locations)!
    ctx.drawLinearGradient(g, start: from, end: to, options: [.drawsBeforeStartLocation, .drawsAfterEndLocation])
}

/// Цикл полос как в шейдере liquid metal: тонкая белая, тонкая тёмная, снова белая и длинный
/// градиент от белого к почти чёрному; между циклами резкий край.
let chromeStops: [(CGFloat, NSColor)] = [
    (0.000, rgb(0xfafaff)), (0.045, rgb(0xfafaff)), (0.050, rgb(0x232326)), (0.080, rgb(0x232326)),
    (0.085, rgb(0xfafaff)), (0.120, rgb(0xfafaff)), (0.130, rgb(0xeeeef4)), (0.480, rgb(0x9d9ea6)),
    (0.780, rgb(0x46464b)), (1.000, rgb(0x161618)),
]

/// Кромка плитки: та же сталь, но без резких полос — свет сверху слева, тень снизу справа.
let bezelStops: [(CGFloat, NSColor)] = [
    (0.00, rgb(0xfafaff)), (0.16, rgb(0xd2d3da)), (0.42, rgb(0x55555b)), (0.55, rgb(0x3c3c41)),
    (0.72, rgb(0x9a9ba3)), (0.86, rgb(0xe6e7ec)), (1.00, rgb(0xfafaff)),
]

func chrome(_ t: CGFloat, stops: [(CGFloat, NSColor)] = chromeStops) -> NSColor {
    let t = t - floor(t)
    var i = 0
    while i < stops.count - 2 && t > stops[i + 1].0 { i += 1 }
    let (t0, c0) = stops[i]
    let (t1, c1) = stops[i + 1]
    return c0.blended(withFraction: max(0, min(1, (t - t0) / max(t1 - t0, 0.0001))), of: c1) ?? c0
}

/// Конический (угловой) градиент клиньями — в CoreGraphics его нет.
func conic(_ ctx: CGContext, center: CGPoint, radius: CGFloat, repetition: CGFloat, phase: CGFloat,
           stops: [(CGFloat, NSColor)] = chromeStops) {
    let n = 1440
    for i in 0..<n {
        let a0 = CGFloat(i) / CGFloat(n) * 2 * .pi
        let a1 = CGFloat(i + 1) / CGFloat(n) * 2 * .pi + 0.003
        ctx.setFillColor(chrome(CGFloat(i) / CGFloat(n) * repetition + phase, stops: stops).cgColor)
        ctx.move(to: center)
        ctx.addLine(to: CGPoint(x: center.x + radius * cos(a0), y: center.y + radius * sin(a0)))
        ctx.addLine(to: CGPoint(x: center.x + radius * cos(a1), y: center.y + radius * sin(a1)))
        ctx.closePath()
        ctx.fillPath()
    }
}

// Геометрия: плитка macOS занимает 824 px из 1024.
let tileRect = CGRect(x: 100, y: 100, width: 824, height: 824)
let tile = CGPath(roundedRect: tileRect, cornerWidth: 186, cornerHeight: 186, transform: nil)
let bezelWidth: CGFloat = 20
let center = CGPoint(x: 512, y: 512)

// Символ микрофона → серая маска.
let symbolCfg = NSImage.SymbolConfiguration(pointSize: 470, weight: .semibold)
let symbol = NSImage(systemSymbolName: "mic.fill", accessibilityDescription: nil)!.withSymbolConfiguration(symbolCfg)!
let symSize = symbol.size
let symRect = CGRect(x: (S - symSize.width) / 2, y: (S - symSize.height) / 2 + 4, width: symSize.width, height: symSize.height)

let maskRep = makeRep(gray: true)
draw(into: maskRep) { ctx in
    ctx.setFillColor(NSColor.black.cgColor)
    ctx.fill(CGRect(x: 0, y: 0, width: S, height: S))
    let white = NSImage(size: symSize)
    white.lockFocus()
    symbol.draw(in: CGRect(origin: .zero, size: symSize))
    NSColor.white.set()
    CGRect(origin: .zero, size: symSize).fill(using: .sourceAtop)
    white.unlockFocus()
    white.draw(in: symRect, from: .zero, operation: .sourceOver, fraction: 1)
}
let symbolMask = maskRep.cgImage!
let full = CGRect(x: 0, y: 0, width: S, height: S)

let final = makeRep()
draw(into: final) { ctx in
    ctx.saveGState()
    ctx.addPath(tile); ctx.clip()

    // 1. Чёрное стекло: чуть светлее к центру-верху, к краям почти чёрное.
    radial(ctx, center: CGPoint(x: 512, y: 640), radius: 760, inner: rgb(0x2a2a2f), outer: rgb(0x050507))
    // Мягкий отсвет сверху и холодный отблеск снизу справа
    radial(ctx, center: CGPoint(x: 330, y: 900), radius: 560, inner: NSColor.white.withAlphaComponent(0.10), outer: NSColor.white.withAlphaComponent(0))
    radial(ctx, center: CGPoint(x: 780, y: 170), radius: 420, inner: rgb(0x9fb4ff, 0.10), outer: rgb(0x9fb4ff, 0))

    // 2. Стальная кромка: хром по кругу, темнее снизу.
    ctx.saveGState()
    ctx.addPath(tile.copy(strokingWithWidth: bezelWidth * 2, lineCap: .round, lineJoin: .round, miterLimit: 10)); ctx.clip()
    conic(ctx, center: center, radius: 800, repetition: 1, phase: 0.62, stops: bezelStops)
    linear(ctx, from: CGPoint(x: 512, y: tileRect.maxY), to: CGPoint(x: 512, y: tileRect.minY),
           colors: [NSColor.black.withAlphaComponent(0), NSColor.black.withAlphaComponent(0.05), NSColor.black.withAlphaComponent(0.45)],
           locations: [0, 0.5, 1])
    ctx.restoreGState()
    // Тень от кромки внутрь плитки — стекло утоплено в металл.
    ctx.saveGState()
    let inner = CGPath(roundedRect: tileRect.insetBy(dx: bezelWidth, dy: bezelWidth), cornerWidth: 168, cornerHeight: 168, transform: nil)
    ctx.addPath(inner); ctx.clip()
    ctx.setShadow(offset: CGSize(width: 0, height: -10), blur: 30, color: NSColor.black.withAlphaComponent(0.9).cgColor)
    ctx.addRect(full.insetBy(dx: -200, dy: -200)); ctx.addPath(inner)
    ctx.setFillColor(NSColor.black.cgColor)
    ctx.fillPath(using: .evenOdd)
    ctx.restoreGState()

    // 3. Микрофон: тень, хромовая заливка «небо/горизонт/земля», диагональный блик.
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -20), blur: 44, color: NSColor.black.withAlphaComponent(0.75).cgColor)
    ctx.clip(to: full, mask: symbolMask)
    ctx.setFillColor(NSColor.white.cgColor)
    ctx.fill(symRect)
    ctx.restoreGState()

    ctx.saveGState()
    ctx.clip(to: full, mask: symbolMask)
    linear(ctx, from: CGPoint(x: 512, y: symRect.maxY), to: CGPoint(x: 512, y: symRect.minY),
           colors: [rgb(0xffffff), rgb(0xe3e4ea), rgb(0xb4b5bd), rgb(0x55545a), rgb(0x2b2a2f), rgb(0x7d7c84), rgb(0xd7d8de), rgb(0xf6f6f9)],
           locations: [0, 0.28, 0.46, 0.50, 0.54, 0.74, 0.92, 1])
    // Диагональные полосы света, как у шейдера (repetition 4, angle 45°)
    ctx.saveGState()
    ctx.setBlendMode(.softLight)
    linear(ctx, from: CGPoint(x: symRect.minX, y: symRect.minY), to: CGPoint(x: symRect.maxX, y: symRect.maxY),
           colors: [rgb(0xffffff, 0.0), rgb(0xffffff, 0.9), rgb(0x000000, 0.5), rgb(0xffffff, 0.8), rgb(0x000000, 0.4), rgb(0xffffff, 0.9), rgb(0xffffff, 0)],
           locations: [0, 0.18, 0.34, 0.5, 0.66, 0.82, 1])
    ctx.restoreGState()
    // Блик на капсуле
    ctx.saveGState()
    ctx.translateBy(x: 462, y: 705); ctx.rotate(by: -0.5)
    ctx.addEllipse(in: CGRect(x: -110, y: -46, width: 220, height: 92)); ctx.clip()
    radial(ctx, center: .zero, radius: 110, inner: NSColor.white.withAlphaComponent(0.95), outer: NSColor.white.withAlphaComponent(0))
    ctx.restoreGState()
    ctx.restoreGState()

    ctx.restoreGState()
}

try! final.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: outPath))
print("ok")
