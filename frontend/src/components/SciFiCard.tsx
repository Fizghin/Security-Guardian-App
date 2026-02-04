import React from 'react';
import { motion } from 'framer-motion';

interface SciFiCardProps {
    children: React.ReactNode;
    title?: string;
    className?: string;
    color?: 'cyan' | 'magenta' | 'electric';
}

const SciFiCard: React.FC<SciFiCardProps> = React.memo(({ children, title, className = "", color = 'cyan' }) => {

    const borderColor = color === 'magenta' ? 'border-magenta/30' :
        color === 'electric' ? 'border-electric/30' : 'border-cyan/30';

    const titleColor = color === 'magenta' ? 'text-magenta' :
        color === 'electric' ? 'text-electric' : 'text-cyan';

    return (
        <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.5 }}
            className={`holo-card ${borderColor} ${className}`}
        >
            <div className="corner-brackets" />

            {title && (
                <div className={`flex items-center justify-between border-b ${borderColor} pb-2 mb-4`}>
                    <h3 className={`text-lg font-orbitron font-bold tracking-widest ${titleColor} neon-text${color === 'magenta' ? '-magenta' : ''}`}>
                        {title}
                    </h3>
                    <div className="flex space-x-1">
                        <div className={`w-2 h-2 rounded-full ${color === 'magenta' ? 'bg-magenta' : 'bg-cyan'} animate-pulse`} />
                        <div className={`w-2 h-2 rounded-full ${color === 'magenta' ? 'bg-magenta/50' : 'bg-cyan/50'}`} />
                    </div>
                </div>
            )}

            <div className="relative z-10">
                {children}
            </div>

            {/* Background Grid Pattern Overlay */}
            <div className="absolute inset-0 hex-grid opacity-20 pointer-events-none z-0" />
        </motion.div>
    );
});

export default SciFiCard;
