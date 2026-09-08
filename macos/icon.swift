// Иконка F5Voice в духе Liquid Glass: стеклянная плитка на цветном градиенте,
// блики, мягкие тени, микрофон-символ SF Symbols со стеклянным бликом.
import Cocoa
import CoreImage

let S: CGFloat = 1024
let outPath = CommandLine.arguments[1]

func rgba(_ r: CGFloat, _ g: CGFloat, _ b: CGFloat, _ a: CGFloat = 1) -> NSColor {
    NSColor(calibratedRed: r / 255, green: g / 255, blue: b / 255, alpha: a)
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

func blur(_ rep: NSBitmapImageRep, _ radius: CGFloat) -> CGImage {
    let ci = CIImage(cgImage: rep.cgImage!)
    let f = CIFilter(name: "CIGaussianBlur")!
    f.setValue(ci, forKey: kCIInputImageKey)
    f.setValue(radius, forKey: kCIInputRadiusKey)
    return CIContext().createCGImage(f.outputImage!.cropped(to: ci.extent), from: ci.extent)!
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

// Геометрия: плитка macOS занимает 824 px из 1024.
let tileRect = CGRect(x: 100, y: 100, width: 824, height: 824)
let tile = CGPath(roundedRect: tileRect, cornerWidth: 186, cornerHeight: 186, transform: nil)
let slabRect = tileRect.insetBy(dx: 88, dy: 88)
let slab = CGPath(roundedRect: slabRect, cornerWidth: 132, cornerHeight: 132, transform: nil)

// Символ микрофона: белый на прозрачном + серая маска для градиентной заливки.
let symbolCfg = NSImage.SymbolConfiguration(pointSize: 470, weight: .semibold)
let symbol = NSImage(systemSymbolName: "mic.fill", accessibilityDescription: nil)!.withSymbolConfiguration(symbolCfg)!
let symSize = symbol.size
let symRect = CGRect(x: (S - symSize.width) / 2, y: (S - symSize.height) / 2 + 6, width: symSize.width, height: symSize.height)

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

// Слой бликов рисуем отдельно и размываем.
let glowRep = makeRep()
draw(into: glowRep) { ctx in
    ctx.addPath(tile); ctx.clip()
    // Широкий блик сверху слева
    radial(ctx, center: CGPoint(x: 300, y: 830), radius: 520, inner: NSColor.white.withAlphaComponent(0.62), outer: NSColor.white.withAlphaComponent(0))
    // Косая полоса-отражение
    ctx.saveGState()
    ctx.translateBy(x: 330, y: 760); ctx.rotate(by: -0.45)
    ctx.addEllipse(in: CGRect(x: -300, y: -70, width: 600, height: 140)); ctx.clip()
    radial(ctx, center: .zero, radius: 300, inner: NSColor.white.withAlphaComponent(0.9), outer: NSColor.white.withAlphaComponent(0))
    ctx.restoreGState()
    // Тёплый отсвет снизу справа
    radial(ctx, center: CGPoint(x: 760, y: 220), radius: 380, inner: rgba(255, 190, 120, 0.35), outer: rgba(255, 190, 120, 0))
}
let glow = blur(glowRep, 28)

let final = makeRep()
draw(into: final) { ctx in
    ctx.saveGState()
    ctx.addPath(tile); ctx.clip()

    // 1. Цветная основа
    linear(ctx, from: CGPoint(x: 200, y: 960), to: CGPoint(x: 820, y: 120),
           colors: [rgba(255, 122, 66), rgba(240, 38, 96), rgba(104, 6, 66)], locations: [0, 0.55, 1])
    // 2. Глубина снизу
    radial(ctx, center: CGPoint(x: 512, y: 120), radius: 700, inner: rgba(80, 0, 50, 0.55), outer: rgba(90, 0, 50, 0))
    // 3. Размытые блики
    ctx.draw(glow, in: CGRect(x: 0, y: 0, width: S, height: S))

    // 4. Стеклянная плита: тень, заливка с градиентом, кромка
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -22), blur: 48, color: rgba(60, 0, 40, 0.35).cgColor)
    ctx.addPath(slab)
    ctx.setFillColor(NSColor.white.withAlphaComponent(0.11).cgColor)
    ctx.fillPath()
    ctx.restoreGState()
    ctx.saveGState()
    ctx.addPath(slab); ctx.clip()
    linear(ctx, from: CGPoint(x: 512, y: slabRect.maxY), to: CGPoint(x: 512, y: slabRect.minY),
           colors: [NSColor.white.withAlphaComponent(0.32), NSColor.white.withAlphaComponent(0.05), NSColor.white.withAlphaComponent(0.12)],
           locations: [0, 0.6, 1])
    // Внутреннее преломление: светлая кромка сверху, тёмная снизу
    ctx.setShadow(offset: CGSize(width: 0, height: -6), blur: 14, color: NSColor.white.withAlphaComponent(0.8).cgColor)
    ctx.setStrokeColor(NSColor.white.withAlphaComponent(0.001).cgColor)
    ctx.setLineWidth(3)
    ctx.addPath(slab); ctx.strokePath()
    ctx.restoreGState()
    // Кромка плиты градиентом: яркая сверху, почти невидимая снизу
    ctx.saveGState()
    ctx.addPath(slab.copy(strokingWithWidth: 5, lineCap: .round, lineJoin: .round, miterLimit: 10)); ctx.clip()
    linear(ctx, from: CGPoint(x: 512, y: slabRect.maxY), to: CGPoint(x: 512, y: slabRect.minY),
           colors: [NSColor.white.withAlphaComponent(0.95), NSColor.white.withAlphaComponent(0.3), NSColor.white.withAlphaComponent(0.55)],
           locations: [0, 0.55, 1])
    ctx.restoreGState()

    // 5. Микрофон: тень, градиентная заливка через маску, блик
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -16), blur: 34, color: rgba(80, 0, 45, 0.55).cgColor)
    ctx.clip(to: CGRect(x: 0, y: 0, width: S, height: S), mask: symbolMask)
    ctx.setFillColor(NSColor.white.cgColor)
    ctx.fill(symRect)
    ctx.restoreGState()
    ctx.saveGState()
    ctx.clip(to: CGRect(x: 0, y: 0, width: S, height: S), mask: symbolMask)
    linear(ctx, from: CGPoint(x: 512, y: symRect.maxY), to: CGPoint(x: 512, y: symRect.minY),
           colors: [NSColor.white, rgba(255, 236, 240), rgba(255, 205, 220)], locations: [0, 0.6, 1])
    // Блик на капсуле микрофона
    ctx.saveGState()
    ctx.translateBy(x: 470, y: 690); ctx.rotate(by: -0.35)
    ctx.addEllipse(in: CGRect(x: -90, y: -40, width: 180, height: 80)); ctx.clip()
    radial(ctx, center: .zero, radius: 90, inner: NSColor.white.withAlphaComponent(0.95), outer: NSColor.white.withAlphaComponent(0))
    ctx.restoreGState()
    ctx.restoreGState()

    // 6. Кромка всей плитки
    ctx.saveGState()
    ctx.addPath(tile.copy(strokingWithWidth: 6, lineCap: .round, lineJoin: .round, miterLimit: 10)); ctx.clip()
    linear(ctx, from: CGPoint(x: 512, y: tileRect.maxY), to: CGPoint(x: 512, y: tileRect.minY),
           colors: [NSColor.white.withAlphaComponent(0.7), NSColor.white.withAlphaComponent(0.15), NSColor.white.withAlphaComponent(0.05)],
           locations: [0, 0.5, 1])
    ctx.restoreGState()

    ctx.restoreGState()
}

try! final.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: outPath))
print("ok")
