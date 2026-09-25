import Testing
@testable import MotionKit

@Suite struct SharedLinkTests {
    @Test func picksTheURLAttachment() {
        #expect(SharedLink.tiktok(in: ["https://vt.tiktok.com/ZSabc123/"]) == "https://vt.tiktok.com/ZSabc123/")
    }

    @Test func findsTheLinkInsideSharedProse() {
        let text = "Check this out https://vt.tiktok.com/ZSabc123/ #fyp"
        #expect(SharedLink.tiktok(in: [text]) == "https://vt.tiktok.com/ZSabc123/")
    }

    @Test func skipsCandidatesWithoutALink() {
        #expect(SharedLink.tiktok(in: ["Sốt Cà Chua", "https://www.tiktok.com/@a/video/1"])
                == "https://www.tiktok.com/@a/video/1")
    }

    @Test func nilWhenNothingIsTikTok() {
        #expect(SharedLink.tiktok(in: ["https://youtu.be/xyz", ""]) == nil)
        #expect(SharedLink.tiktok(in: []) == nil)
    }
}
