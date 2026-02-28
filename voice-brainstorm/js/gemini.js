const Gemini = {
    async generateQuestion(apiKey, agenda, qaPairs, masterPrompt) {
        const model = 'gemini-2.0-flash';
        const url = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${apiKey}`;

        let prompt = 'あなたはブレインストーミングの壁打ち相手です。';
        prompt += 'ユーザーのアイデアを深掘りし、新しい視点を提供する質問をしてください。\n\n';

        if (masterPrompt) {
            prompt += `【指示】\n${masterPrompt}\n\n`;
        }

        prompt += `【アジェンダ】\n${agenda}\n\n`;

        if (qaPairs.length > 0) {
            prompt += '【これまでの質問と回答】\n';
            qaPairs.forEach((qa, i) => {
                prompt += `Q${i + 1}: ${qa.question}\n`;
                prompt += `A${i + 1}: ${qa.answer || '(スキップ)'}\n\n`;
            });
        }

        prompt += '上記の文脈を踏まえて、次の質問を1つだけ生成してください。\n';
        prompt += 'これまでの質問と重複しない、より深い洞察を引き出す質問をしてください。\n';
        prompt += '質問文のみを返してください。前置きや説明は不要です。';

        const response = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                contents: [{ role: 'user', parts: [{ text: prompt }] }],
                generationConfig: {
                    temperature: 0.8,
                    maxOutputTokens: 512,
                },
            }),
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.error?.message || `API Error: ${response.status}`);
        }

        const data = await response.json();
        const text = data.candidates?.[0]?.content?.parts?.[0]?.text;
        if (!text) throw new Error('質問の生成に失敗しました');
        return text.trim();
    },
};
