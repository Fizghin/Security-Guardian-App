import React, { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';

interface SubtitleOverlayProps {
    text?: string;
    timestamp?: number; // Unix timestamp provided by backend
}

export const SubtitleOverlay: React.FC<SubtitleOverlayProps> = ({ text, timestamp }) => {
    const [visibleText, setVisibleText] = useState<string | null>(null);
    const [key, setKey] = useState(0);

    useEffect(() => {
        if (text && timestamp) {
            // Only show if the message is recent (within last 10 seconds)
            const now = Date.now() / 1000;
            if (now - timestamp < 10) {
                setVisibleText(text);
                setKey(prev => prev + 1); // Force re-render for animation reset

                // Auto-clear after 6 seconds of display
                const timer = setTimeout(() => {
                    setVisibleText(null);
                }, 6000);
                return () => clearTimeout(timer);
            }
        }
    }, [text, timestamp]);

    return (
        <AnimatePresence>
            {visibleText && (
                <motion.div
                    key={key}
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -10 }}
                    transition={{ duration: 0.5 }}
                    className="absolute bottom-16 left-0 right-0 mx-auto w-3/4 max-w-2xl text-center pointer-events-none z-20"
                >
                    <div className="bg-black/70 backdrop-blur-sm border border-cyan-500/30 text-cyan-100 px-6 py-4 rounded-xl shadow-[0_0_15px_rgba(6,182,212,0.3)]">
                        <div className="text-xs uppercase tracking-widest text-cyan-400 mb-1 font-bold">
                            AI Guardian Speaking
                        </div>
                        <p className="text-lg md:text-xl font-mono leading-relaxed drop-shadow-md">
                            "{visibleText}"
                        </p>
                    </div>
                </motion.div>
            )}
        </AnimatePresence>
    );
};
