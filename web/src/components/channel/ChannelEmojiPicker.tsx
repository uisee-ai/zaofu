import emojiData from "@emoji-mart/data";
import Picker from "@emoji-mart/react";

export function ChannelEmojiPicker({
  onEmojiSelect,
}: {
  onEmojiSelect: (selection: { native?: string; shortcodes?: string }) => void;
}) {
  return (
    <Picker
      data={emojiData}
      emojiButtonSize={30}
      emojiSize={21}
      locale="zh"
      maxFrequentRows={2}
      navPosition="top"
      onEmojiSelect={onEmojiSelect}
      previewPosition="none"
      searchPosition="top"
      set="native"
      theme="light"
    />
  );
}
