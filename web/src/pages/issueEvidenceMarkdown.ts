// Display-only compatibility for comments emitted by the old evidence publisher.
export function repairGeneratedEvidenceComment(content: string): string {
  if (!content.startsWith('<!-- zf-self-issue-evidence:') || !content.includes('## ZaoFu evidence')) return content;
  const footer = content.lastIndexOf('\nThe following image/video files are attached to this comment:');
  const opening = content.indexOf('\n```text\n');
  if (footer >= 0 && opening >= 0 && opening < footer) {
    const closing = content.lastIndexOf('\n```\n', footer);
    let fenceLength = 0;
    for (const line of content.slice(0, footer).split('\n')) {
      const match = line.match(/^(`{3,})(.*)$/);
      if (!match) continue;
      if (!fenceLength) fenceLength = match[1].length;
      else if (match[1].length >= fenceLength && !match[2].trim()) fenceLength = 0;
    }
    if (fenceLength && closing > opening) {
      const excerpt = content.slice(opening + '\n```text\n'.length, closing);
      const fence = '`'.repeat(Math.max(3, ...Array.from(excerpt.matchAll(/`+/g), m => m[0].length + 1)));
      content = content.slice(0, opening) + `\n${fence}text\n${excerpt}\n${fence}\n` + content.slice(closing + 5);
    }
  }
  // The old publisher retained the media-only warning even after Release success.
  const downloads = content.split('\n### ')[0];
  for (const match of downloads.matchAll(/^- \[([^\]\n]+)\]\(https:\/\/github\.com\/[^\s)]+\/releases\/download\/[^\s)]+\)/gm)) {
    content = content.replace(`- ${match[1]}: GitHub CLI uploads images and videos only.\n`, '');
  }
  const media = Array.from(downloads.matchAll(/^- \[([^\]\n]+)\]\((https:\/\/github\.com\/[^\s)]+\/releases\/download\/[^\s)]+)\)/gm),
    match => ({name:match[1],url:match[2]}));
  const start = content.lastIndexOf('\nThe following image/video files are attached to this comment:');
  if (start >= 0) {
    const images = media.filter(item => /\.(png|jpe?g|gif|webp)$/i.test(item.name));
    const videos = media.filter(item => /\.(webm|mp4|mov|m4v)$/i.test(item.name));
    let tail = content.slice(start).replace(/!\[([^\]]*)\]\(https:\/\/github\.com\/user-attachments\/assets\/[^)]+\)/g, (original, alt:string) => {
      const item = images.find(item => alt.endsWith(item.name.replace(/\.[^.]+$/, ''))) || (images.length === 1 ? images[0] : undefined);
      return item ? `![${alt}](${item.url})` : original;
    });
    let video = 0;
    tail = tail.replace(/^https:\/\/github\.com\/user-attachments\/assets\/[^\s]+$/gm,
      original => videos[video] ? `[▶ Play video](${videos[video++].url})` : original);
    content = content.slice(0, start) + tail;
  }
  return content.replace(/The following files could not be attached automatically:\n\n+(?=\n|https:|!\[|$)/g, '');
}
