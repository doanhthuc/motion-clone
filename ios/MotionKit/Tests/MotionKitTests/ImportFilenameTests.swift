import Testing
@testable import MotionKit

@Suite struct ImportFilenameTests {
    @Test func emptyProviderNameGetsASafeFallback() {
        #expect(ImportedFilename.sanitize(nil) == "upload.bin")
        #expect(ImportedFilename.sanitize("   ") == "upload.bin")
    }

    @Test func pathComponentsAreRemovedWithoutChangingTheExtension() {
        #expect(ImportedFilename.sanitize("/private/tmp/áo dài.PNG") == "áo dài.PNG")
        #expect(ImportedFilename.sanitize(#"C:\temp\driver.mp4"#) == "driver.mp4")
    }
}
