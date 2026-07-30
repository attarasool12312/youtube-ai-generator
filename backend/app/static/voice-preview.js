document.addEventListener("DOMContentLoaded", () => {
  const audio = new Audio();
  audio.preload = "none";

  let activeButton = null;

  function resetButton() {
    if (activeButton) {
      activeButton.textContent = "▶ Preview";
      activeButton.classList.remove("is-playing");
    }

    activeButton = null;
  }

  function getVoice(button) {
    if (button.dataset.voice) {
      return button.dataset.voice;
    }

    const selectName = button.dataset.voiceSelect;
    const select = document.querySelector(`[name="${selectName}"]`);

    return select ? select.value : "";
  }

  document.querySelectorAll(".voice-preview-button").forEach((button) => {
    button.addEventListener("click", async () => {
      const voice = getVoice(button);

      if (!voice) return;

      const source = `/static/voice-previews/${encodeURIComponent(voice)}.mp3`;

      if (activeButton === button && !audio.paused) {
        audio.pause();
        audio.currentTime = 0;
        resetButton();
        return;
      }

      audio.pause();
      audio.currentTime = 0;
      resetButton();

      activeButton = button;
      button.textContent = "■ Stop";
      button.classList.add("is-playing");

      audio.src = source;

      try {
        await audio.play();
      } catch (error) {
        console.error("Voice preview failed:", error);
        resetButton();
      }
    });
  });

  audio.addEventListener("ended", resetButton);
  audio.addEventListener("error", resetButton);
});
