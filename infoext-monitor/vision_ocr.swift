import AppKit
import Foundation
import Vision

func option(_ name: String, in arguments: [String]) -> String? {
    guard let index = arguments.firstIndex(of: name), index + 1 < arguments.count else {
        return nil
    }
    return arguments[index + 1]
}

let arguments = Array(CommandLine.arguments.dropFirst())
guard let recognitionLevel = option("--recognition-level", in: arguments),
      let language = option("--language", in: arguments),
      let textHeightValue = option("--minimum-text-height", in: arguments),
      let candidateLimitValue = option("--candidate-limit", in: arguments),
      let minimumTextHeight = Float(textHeightValue),
      let candidateLimit = Int(candidateLimitValue),
      (recognitionLevel == "accurate" || recognitionLevel == "fast"),
      candidateLimit >= 1,
      (0...1).contains(minimumTextHeight) else {
    fputs("Invalid Apple Vision OCR options.\n", stderr)
    exit(2)
}

let imageData = FileHandle.standardInput.readDataToEndOfFile()
guard let image = NSImage(data: imageData),
      let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    fputs("Invalid CAPTCHA image.\n", stderr)
    exit(2)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = recognitionLevel == "accurate" ? .accurate : .fast
request.usesLanguageCorrection = option("--language-correction", in: arguments) == "enable"
request.recognitionLanguages = [language]
request.minimumTextHeight = minimumTextHeight

do {
    try VNImageRequestHandler(cgImage: cgImage).perform([request])
    let observations = (request.results ?? [])
        .sorted { $0.boundingBox.minX < $1.boundingBox.minX }
    let candidates = observations.first?.topCandidates(candidateLimit) ?? []
    let response: [String: Any] = [
        "candidates": candidates.map { ["text": $0.string, "confidence": $0.confidence] },
        "observations": observations.map { observation in
            ["candidates": observation.topCandidates(candidateLimit).map {
                ["text": $0.string, "confidence": $0.confidence]
            }]
        }
    ]
    let data = try JSONSerialization.data(withJSONObject: response)
    FileHandle.standardOutput.write(data)
} catch {
    fputs("Apple Vision OCR failed.\n", stderr)
    exit(1)
}
