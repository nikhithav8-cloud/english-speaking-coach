const micBtn = document.getElementById("micBtn");
const chatBox = document.getElementById("chat-box");
const audioPlayer = document.getElementById("audioPlayer");

const recognition = new (window.SpeechRecognition || window.webkitSpeechRecognition)();
recognition.lang = "en-US";

micBtn.onclick = () => {
    recognition.start();
};

recognition.onresult = async (event) => {
    const text = event.results[0][0].transcript;

    addMessage(text, "user");

    const response = await fetch("/process", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text })
    });

    const data = await response.json();

    addMessage(data.reply, "bot");

    audioPlayer.src = data.audio;
    audioPlayer.play();
};

function addMessage(text, type) {
    const div = document.createElement("div");
    div.className = `message ${type}`;
    div.innerText = text;
    chatBox.appendChild(div);
    chatBox.scrollTop = chatBox.scrollHeight;
}
