const App = {
    currentDiscussion: null,

    init() {
        Speech.init();
        this.bindEvents();
        this.showMainScreen();
    },

    bindEvents() {
        document.getElementById('btn-new-discussion').addEventListener('click', () => this.createNewDiscussion());
        document.getElementById('btn-master-prompt').addEventListener('click', () => this.showMasterPromptModal());
        document.getElementById('btn-api-key').addEventListener('click', () => this.showApiKeyModal());

        document.getElementById('btn-back').addEventListener('click', () => this.showMainScreen());
        document.getElementById('btn-start').addEventListener('click', () => this.startDiscussion());
        document.getElementById('btn-voice').addEventListener('click', () => this.startVoiceInput());
        document.getElementById('btn-complete').addEventListener('click', () => this.completeVoiceInput());
        document.getElementById('btn-next').addEventListener('click', () => this.skipQuestion());

        document.getElementById('btn-save-prompt').addEventListener('click', () => this.saveMasterPrompt());
        document.getElementById('btn-cancel-prompt').addEventListener('click', () => this.hideModals());
        document.getElementById('btn-save-api-key').addEventListener('click', () => this.saveApiKey());
        document.getElementById('btn-cancel-api-key').addEventListener('click', () => this.hideModals());

        document.querySelectorAll('.modal').forEach(modal => {
            modal.addEventListener('click', (e) => {
                if (e.target === modal) this.hideModals();
            });
        });
    },

    // Screen navigation
    showMainScreen() {
        Speech.stopSpeaking();
        Speech.stop();
        this.currentDiscussion = null;
        document.getElementById('main-screen').classList.add('active');
        document.getElementById('discussion-screen').classList.remove('active');
        this.renderDiscussionList();
    },

    showDiscussionScreen(discussion) {
        this.currentDiscussion = discussion;
        document.getElementById('main-screen').classList.remove('active');
        document.getElementById('discussion-screen').classList.add('active');

        const agendaSection = document.getElementById('agenda-section');
        const qaSection = document.getElementById('qa-section');

        if (discussion.qaPairs.length > 0) {
            document.getElementById('agenda-input').value = discussion.agenda;
            agendaSection.classList.add('compact');
            qaSection.classList.remove('hidden');
            this.renderQAList();
            this.generateNextQuestion();
        } else if (discussion.agenda) {
            document.getElementById('agenda-input').value = discussion.agenda;
            agendaSection.classList.remove('compact');
            qaSection.classList.add('hidden');
        } else {
            document.getElementById('agenda-input').value = '';
            agendaSection.classList.remove('compact');
            qaSection.classList.add('hidden');
        }

        document.getElementById('discussion-title').textContent = discussion.title || '新しい議論';
    },

    // Discussion list
    renderDiscussionList() {
        const list = document.getElementById('discussion-list');
        const discussions = Storage.getDiscussions();

        if (discussions.length === 0) {
            list.innerHTML = '<div class="empty-state"><p>まだ議論がありません</p><p class="hint">「新しい議論を始める」から始めましょう</p></div>';
            return;
        }

        list.innerHTML = discussions.map(d => `
            <div class="discussion-item" data-id="${d.id}">
                <div class="discussion-info" data-id="${d.id}">
                    <div class="discussion-item-title">${this.escapeHtml(d.title || d.agenda || '無題の議論')}</div>
                    <div class="discussion-meta">
                        <span>${d.qaPairs.length} 件のQ&A</span>
                        <span>${this.formatDate(d.updatedAt)}</span>
                    </div>
                </div>
                <button class="btn btn-delete" data-id="${d.id}" title="削除">&times;</button>
            </div>
        `).join('');

        list.querySelectorAll('.discussion-info').forEach(el => {
            el.addEventListener('click', () => {
                const discussion = Storage.getDiscussion(el.dataset.id);
                if (discussion) this.showDiscussionScreen(discussion);
            });
        });

        list.querySelectorAll('.btn-delete').forEach(el => {
            el.addEventListener('click', (e) => {
                e.stopPropagation();
                if (confirm('この議論を削除しますか？')) {
                    Storage.deleteDiscussion(el.dataset.id);
                    this.renderDiscussionList();
                }
            });
        });
    },

    // Create new discussion
    createNewDiscussion() {
        const apiKey = Storage.getApiKey();
        if (!apiKey) {
            alert('まずAPIキーを設定してください。');
            this.showApiKeyModal();
            return;
        }
        const discussion = Storage.createDiscussion();
        this.showDiscussionScreen(discussion);
    },

    // Start discussion
    async startDiscussion() {
        const agenda = document.getElementById('agenda-input').value.trim();
        if (!agenda) {
            alert('アジェンダを入力してください。');
            return;
        }

        const apiKey = Storage.getApiKey();
        if (!apiKey) {
            this.showApiKeyModal();
            return;
        }

        const title = agenda.length > 50 ? agenda.substring(0, 50) + '...' : agenda;
        this.currentDiscussion = Storage.updateDiscussion(this.currentDiscussion.id, {
            agenda,
            title,
        });

        document.getElementById('discussion-title').textContent = title;
        document.getElementById('agenda-section').classList.add('compact');
        document.getElementById('qa-section').classList.remove('hidden');

        await this.generateNextQuestion();
    },

    // Generate next question
    async generateNextQuestion() {
        const loading = document.getElementById('loading');
        const currentQuestion = document.getElementById('current-question');

        loading.classList.remove('hidden');
        currentQuestion.classList.add('hidden');

        try {
            const apiKey = Storage.getApiKey();
            const masterPrompt = Storage.getMasterPrompt();
            const question = await Gemini.generateQuestion(
                apiKey,
                this.currentDiscussion.agenda,
                this.currentDiscussion.qaPairs,
                masterPrompt
            );

            currentQuestion.querySelector('.question-text').textContent = question;
            currentQuestion.classList.remove('hidden');
            loading.classList.add('hidden');

            this.resetVoiceUI();
            await Speech.speak(question);
        } catch (error) {
            loading.classList.add('hidden');
            currentQuestion.classList.remove('hidden');
            currentQuestion.querySelector('.question-text').textContent = `エラー: ${error.message}`;
            this.resetVoiceUI();
            console.error('Question generation error:', error);
        }
    },

    // Voice input
    startVoiceInput() {
        if (!Speech.isSupported()) {
            alert('お使いのブラウザは音声認識に対応していません。Chrome または Edge をお使いください。');
            return;
        }

        Speech.stopSpeaking();

        Speech.onTranscriptUpdate = (text) => {
            const preview = document.getElementById('transcript-preview');
            preview.textContent = text;
            preview.classList.remove('hidden');
        };

        Speech.onEnd = (transcript) => {
            this.handleVoiceResult(transcript);
        };

        if (Speech.start()) {
            document.getElementById('btn-voice').classList.add('hidden');
            document.getElementById('btn-complete').classList.remove('hidden');
            document.getElementById('voice-status').classList.remove('hidden');
            document.getElementById('btn-next').classList.add('disabled');
        }
    },

    completeVoiceInput() {
        Speech.stop();
    },

    async handleVoiceResult(transcript) {
        this.resetVoiceUI();
        if (!transcript.trim()) return;
        await this.saveAnswer(transcript.trim());
    },

    async skipQuestion() {
        if (document.getElementById('btn-next').classList.contains('disabled')) return;
        Speech.stopSpeaking();
        await this.saveAnswer(null);
    },

    async saveAnswer(answer) {
        const questionText = document.getElementById('current-question').querySelector('.question-text').textContent;

        const qaPairs = [...this.currentDiscussion.qaPairs, {
            question: questionText,
            answer: answer,
            timestamp: new Date().toISOString(),
        }];

        this.currentDiscussion = Storage.updateDiscussion(this.currentDiscussion.id, { qaPairs });
        this.renderQAList();
        await this.generateNextQuestion();
    },

    resetVoiceUI() {
        document.getElementById('btn-voice').classList.remove('hidden');
        document.getElementById('btn-complete').classList.add('hidden');
        document.getElementById('voice-status').classList.add('hidden');
        document.getElementById('transcript-preview').classList.add('hidden');
        document.getElementById('transcript-preview').textContent = '';
        document.getElementById('btn-next').classList.remove('disabled');
    },

    // Render Q&A list
    renderQAList() {
        const list = document.getElementById('qa-list');
        if (!this.currentDiscussion || this.currentDiscussion.qaPairs.length === 0) {
            list.innerHTML = '';
            return;
        }

        const pairs = this.currentDiscussion.qaPairs;
        list.innerHTML = [...pairs].reverse().map((qa, i) => `
            <div class="qa-item">
                <div class="qa-question">
                    <span class="qa-label">Q${pairs.length - i}</span>
                    <span>${this.escapeHtml(qa.question)}</span>
                </div>
                <div class="qa-answer ${qa.answer ? '' : 'skipped'}">
                    <span class="qa-label">A</span>
                    <span>${qa.answer ? this.escapeHtml(qa.answer) : 'スキップ'}</span>
                </div>
            </div>
        `).join('');
    },

    // Modals
    showMasterPromptModal() {
        document.getElementById('master-prompt-input').value = Storage.getMasterPrompt();
        document.getElementById('modal-master-prompt').classList.remove('hidden');
    },

    showApiKeyModal() {
        document.getElementById('api-key-input').value = Storage.getApiKey();
        document.getElementById('modal-api-key').classList.remove('hidden');
    },

    hideModals() {
        document.querySelectorAll('.modal').forEach(m => m.classList.add('hidden'));
    },

    saveMasterPrompt() {
        const prompt = document.getElementById('master-prompt-input').value.trim();
        Storage.saveMasterPrompt(prompt);
        this.hideModals();
    },

    saveApiKey() {
        const key = document.getElementById('api-key-input').value.trim();
        if (!key) {
            alert('APIキーを入力してください。');
            return;
        }
        Storage.saveApiKey(key);
        this.hideModals();
    },

    // Utilities
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },

    formatDate(isoString) {
        const date = new Date(isoString);
        const now = new Date();
        const diff = now - date;

        if (diff < 60000) return 'たった今';
        if (diff < 3600000) return `${Math.floor(diff / 60000)}分前`;
        if (diff < 86400000) return `${Math.floor(diff / 3600000)}時間前`;

        return date.toLocaleDateString('ja-JP', {
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
        });
    },
};

document.addEventListener('DOMContentLoaded', () => App.init());
