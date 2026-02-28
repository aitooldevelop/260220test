const Speech = {
    recognition: null,
    synthesis: window.speechSynthesis,
    isListening: false,
    transcript: '',
    onTranscriptUpdate: null,
    onEnd: null,

    init() {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRecognition) {
            console.warn('Speech Recognition is not supported in this browser.');
            return false;
        }

        this.recognition = new SpeechRecognition();
        this.recognition.lang = 'ja-JP';
        this.recognition.continuous = true;
        this.recognition.interimResults = true;

        this.recognition.onresult = (event) => {
            let interim = '';
            let final = '';
            for (let i = 0; i < event.results.length; i++) {
                const result = event.results[i];
                if (result.isFinal) {
                    final += result[0].transcript;
                } else {
                    interim += result[0].transcript;
                }
            }
            this.transcript = final;
            const displayText = final + interim;

            if (this.onTranscriptUpdate) {
                this.onTranscriptUpdate(displayText);
            }

            if (final.includes('以上です')) {
                this.transcript = final.replace(/以上です[。]*$/g, '').trim();
                this.stop();
            }
        };

        this.recognition.onerror = (event) => {
            console.error('Speech recognition error:', event.error);
            if (event.error !== 'no-speech') {
                this.isListening = false;
                if (this.onEnd) this.onEnd(this.transcript);
            }
        };

        this.recognition.onend = () => {
            if (this.isListening) {
                try {
                    this.recognition.start();
                } catch (e) {
                    this.isListening = false;
                    if (this.onEnd) this.onEnd(this.transcript);
                }
            } else {
                if (this.onEnd) this.onEnd(this.transcript);
            }
        };

        return true;
    },

    start() {
        if (!this.recognition) return false;
        this.transcript = '';
        this.isListening = true;
        try {
            this.recognition.start();
            return true;
        } catch (e) {
            console.error('Failed to start recognition:', e);
            this.isListening = false;
            return false;
        }
    },

    stop() {
        this.isListening = false;
        if (this.recognition) {
            try {
                this.recognition.stop();
            } catch (e) {
                // ignore
            }
        }
    },

    speak(text) {
        return new Promise((resolve) => {
            if (!this.synthesis) {
                resolve();
                return;
            }
            this.synthesis.cancel();

            const utterance = new SpeechSynthesisUtterance(text);
            utterance.lang = 'ja-JP';
            utterance.rate = 1.0;
            utterance.pitch = 1.0;
            utterance.onend = () => resolve();
            utterance.onerror = () => resolve();
            this.synthesis.speak(utterance);
        });
    },

    stopSpeaking() {
        if (this.synthesis) {
            this.synthesis.cancel();
        }
    },

    isSupported() {
        return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
    },
};
