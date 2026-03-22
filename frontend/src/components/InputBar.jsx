
import React, { useState, useRef, useEffect } from 'react';
import { Paperclip, User, Code, Zap, ArrowUp } from 'lucide-react';

const InputBar = ({ onSend, disabled, persona, setPersona }) => {
    const [text, setText] = useState('');
    const [attachments, setAttachments] = useState([]);
    const fileInputRef = useRef(null);

    const handleSubmit = (e) => {
        e.preventDefault();
        if ((text.trim() || attachments.length > 0) && !disabled) {
            onSend(text, attachments, persona);
            setText('');
            setAttachments([]);
        }
    };

    const canSend = (text.trim() || attachments.length > 0) && !disabled;

    const handleFileChange = (e) => {
        const files = Array.from(e.target.files || []);
        setAttachments(prev => [...prev, ...files]);
        e.target.value = null;
    };

    const removeAttachment = (idx) => {
        setAttachments(prev => prev.filter((_, i) => i !== idx));
    };

    const [showPersonaMenu, setShowPersonaMenu] = useState(false);
    const containerRef = useRef(null);

    // close menu when clicking outside
    useEffect(() => {
        const handleClick = (e) => {
            if (containerRef.current && !containerRef.current.contains(e.target)) {
                setShowPersonaMenu(false);
            }
        };
        document.addEventListener('mousedown', handleClick);
        return () => document.removeEventListener('mousedown', handleClick);
    }, []);

    // simple mapping for nicer placeholder text
    const personaLabels = {
        default: 'Normal mode',
        brutal: 'Brutally Honest mode',
        mentor: 'Mentor mode ',
        debate: 'Debate Mode',
        comedy: 'Comedian mode',
        minimalist: 'Minimalist mode',
        overexplainer: 'Overexplainer mode',
        rage_bait: 'Rage Bait mode',
    };

    return (
        <div className="fixed bottom-1 right-6 w-full max-w-6xl px-5 flex justify-end">
            {/* Main Input Card */}
            <form
                onSubmit={handleSubmit}
                className="w-full relative lux-card p-1.5 focus-within:border-[rgba(0,0,0,0.1)] transition-colors duration-300 fade-in-up stagger-1"
            >
                {/* Textarea & Attachments Container */}
                <div className="px-4 pt-3 pb-14 flex flex-col">

    {/* Active Persona Indicator */}
    <div className="flex items-center gap-2 mb-2 text-[12px] text-text-muted">
        <span className="w-2 h-2 rounded-full bg-[var(--accent)]"></span>
        <span>{personaLabels[persona] || personaLabels.default}</span>
    </div>

    <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={`How can I help you today?`}
        disabled={disabled}
        rows={1}
        onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                handleSubmit(e);
            }
        }}
        className="w-full bg-transparent text-[15px] text-text-main placeholder-[rgba(0,0,0,0.4)] resize-none focus:outline-none leading-relaxed font-normal"
        style={{ height: '48px', maxHeight: '160px' }}
        onInput={(e) => {
            e.target.style.height = '48px';
            e.target.style.height = Math.min(e.target.scrollHeight, 160) + 'px';
        }}
    />

                    {/* Attachments preview */}
                    {attachments.length > 0 && (
                        <div className="flex flex-wrap gap-2 mt-2">
                            {attachments.map((file, i) =>
                                <div
                                    key={i}
                                    className="flex items-center gap-1 bg-[rgba(0,0,0,0.03)] px-2 py-1 rounded-md text-[11px] text-text-dim border border-black/5"
                                >
                                    <span className="truncate max-w-[150px]">{file.name}</span>
                                    <button
                                        type="button"
                                        onClick={() => removeAttachment(i)}
                                        className="w-4 h-4 flex items-center justify-center text-xs hover:text-text-main"
                                    >
                                        ×
                                    </button>
                                </div>
                            )}
                        </div>
                    )}
                </div>

                {/* Bottom Row tools inside input card */}
                <div ref={containerRef} className="absolute bottom-3 left-3 flex items-center gap-1">
                    <button
                        type="button"
                        onClick={() => fileInputRef.current && fileInputRef.current.click()}
                        className="w-8 h-8 flex items-center justify-center rounded-lg text-text-muted hover:text-text-main hover:bg-[rgba(0,0,0,0.05)] transition-all duration-200"
                    >
                        <Paperclip className="w-4 h-4" />
                    </button>
                    {/* persona button + dropdown */}
                    <div className="relative flex items-center">
                        <button
                            type="button"
                            onClick={() => setShowPersonaMenu(prev => !prev)}
                            className="w-8 h-8 flex items-center justify-center rounded-lg text-text-muted hover:text-text-main hover:bg-[rgba(0,0,0,0.05)] transition-all duration-200"
                        >
                            <User className="w-4 h-4" />
                        </button>
                        {/* dropdown positioned directly above the user button */}
                        {showPersonaMenu && (
                            <div className="absolute bottom-full left-0 mb-1 z-50 w-36 bg-white border border-black/10 rounded-md shadow-lg overflow-hidden">
                                {Object.entries(personaLabels).map(([key, label]) => (
                                    <div
                                        key={key}
                                        onClick={() => {
                                            setPersona && setPersona(key);
                                            setShowPersonaMenu(false);
                                        }}
                                        className="px-3 py-2 text-xs text-text-main hover:bg-black/5 cursor-pointer transition-colors"
                                    >
                                        {label}
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                    <ToolBtn icon={Code} />
                    <ToolBtn icon={Zap} />
                </div>


                {/* Send Button */}
                <div className="absolute bottom-3 right-3">
                    <button
                        type="submit"
                        disabled={!canSend}
                        className={`w-9 h-9 rounded-full flex items-center justify-center transition-all duration-300 ${canSend
                            ? 'bg-black text-white shadow-lg scale-105 hover:scale-110'
                            : 'bg-[rgba(0,0,0,0.05)] text-text-muted/30 cursor-not-allowed'
                            }`}
                    >
                        <ArrowUp className="w-5 h-5" strokeWidth={2.5} />
                    </button>
                </div>
                {/* hidden file input */}
                <input
                    type="file"
                    multiple
                    ref={fileInputRef}
                    className="hidden"
                    onChange={handleFileChange}
                />
            </form>


        </div>
    );
};

const ToolBtn = ({ icon: Icon }) => (
    <button
        type="button"
        className="w-8 h-8 flex items-center justify-center rounded-lg text-text-muted hover:text-text-main hover:bg-[rgba(0,0,0,0.05)] transition-all duration-200"
    >
        <Icon className="w-4 h-4" />
    </button>
);

export default InputBar;
