import { githubMarkdownForDisplay } from "../src/pages/issueTriageModel.js";

function assertEqual(actual: string, expected: string): void {
  if (actual !== expected) throw new Error(`Expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
}

const githubImage = '<img width="211" alt="Image" src="https://github.com/user-attachments/assets/example" />';
assertEqual(
  githubMarkdownForDisplay(githubImage, "project one"),
  "[![Image](/api/projects/project%20one/issue-triage/attachment?url=https%3A%2F%2Fgithub.com%2Fuser-attachments%2Fassets%2Fexample)](https://github.com/user-attachments/assets/example)",
);

const unsafeImage = '<img alt="private" src="https://example.com/private.png" />';
assertEqual(githubMarkdownForDisplay(unsafeImage, "project one"), unsafeImage);

const markdownImage = "![capture](https://github.com/user-attachments/assets/example.png)";
assertEqual(
  githubMarkdownForDisplay(markdownImage, "project one"),
  "[![capture](/api/projects/project%20one/issue-triage/attachment?url=https%3A%2F%2Fgithub.com%2Fuser-attachments%2Fassets%2Fexample.png)](https://github.com/user-attachments/assets/example.png)",
);

const githubVideo = '<video controls src="https://github.com/user-attachments/assets/example-video"></video>';
assertEqual(
  githubMarkdownForDisplay(githubVideo, "project one"),
  "[▶ Play video](/api/projects/project%20one/issue-triage/attachment?url=https%3A%2F%2Fgithub.com%2Fuser-attachments%2Fassets%2Fexample-video)",
);

const bareVideoAttachment = [
  "The following image/video files are attached to this comment:",
  "- screen-recording-2026-09-02.webm",
  "https://github.com/user-attachments/assets/example-video",
].join("\\n");
assertEqual(
  githubMarkdownForDisplay(bareVideoAttachment, "project one"),
  [
    "The following image/video files are attached to this comment:",
    "- screen-recording-2026-09-02.webm",
    "[▶ Play video](/api/projects/project%20one/issue-triage/attachment?url=https%3A%2F%2Fgithub.com%2Fuser-attachments%2Fassets%2Fexample-video)",
  ].join("\\n"),
);

const unsafeVideo = '<video src="https://evil.test/video.webm"></video>';
assertEqual(githubMarkdownForDisplay(unsafeVideo, "project one"), unsafeVideo);

console.log("issueTriageModel.test.ts OK");
