import React from 'react';
import {
    HomeIcon,
    ChartBarIcon,
    VideoCameraIcon,
    Cog6ToothIcon,
    DocumentTextIcon,
    ShieldCheckIcon,
    XMarkIcon
} from '@heroicons/react/24/outline';
import classNames from 'classnames';

interface SidebarProps {
    activeView: string;
    onNavigate: (view: string) => void;
    isOpen?: boolean;
    onClose?: () => void;
}

const Sidebar: React.FC<SidebarProps> = React.memo(({ activeView, onNavigate, isOpen = false, onClose }) => {
    const menuItems = [
        { id: 'surveillance', label: 'SURVEILLANCE', icon: HomeIcon },
        { id: 'analytics', label: 'INTEL / LOGS', icon: ChartBarIcon },
        { id: 'archive', label: 'ARCHIVES', icon: VideoCameraIcon },
        { id: 'reports', label: 'REPORTS', icon: DocumentTextIcon },
        { id: 'config', label: 'SYSTEM CONFIG', icon: Cog6ToothIcon },
    ];

    return (
        <>
            <div
                className={classNames(
                    "fixed inset-0 bg-black/80 z-40 backdrop-blur-sm transition-opacity duration-300 lg:hidden",
                    { "opacity-100 pointer-events-auto": isOpen, "opacity-0 pointer-events-none": !isOpen }
                )}
                onClick={onClose}
            />

            <div className={classNames(
                "fixed lg:static top-0 left-0 h-full w-72 bg-[#050510]/95 border-r border-cyan/20 backdrop-blur-xl z-50 shadow-[4px_0_24px_rgba(0,0,0,0.5)] transition-transform duration-300 ease-out lg:transform-none flex flex-col",
                { "translate-x-0": isOpen, "-translate-x-full lg:translate-x-0": !isOpen }
            )}>
                <div className="p-6 border-b border-cyan/20 flex items-center justify-between bg-cyan/5">
                    <div className="flex items-center space-x-3">
                        <ShieldCheckIcon className="w-8 h-8 text-cyan animate-pulse" />
                        <div>
                            <h1 className="text-xl font-orbitron font-bold text-white tracking-wider">GUARDIAN</h1>
                            <div className="text-[10px] text-cyan/60 font-mono tracking-[0.3em]">SYSTEM V2.0</div>
                        </div>
                    </div>
                    <button onClick={onClose} className="lg:hidden text-gray-400 hover:text-cyan p-1">
                        <XMarkIcon className="w-6 h-6" />
                    </button>
                </div>

                <nav className="flex-1 py-6 space-y-2 px-3 overflow-y-auto custom-scrollbar">
                    {menuItems.map((item) => {
                        const isActive = activeView === item.id;
                        return (
                            <button
                                key={item.id}
                                onClick={() => onNavigate(item.id)}
                                className={classNames(
                                    "w-full flex items-center space-x-3 px-4 py-3 rounded transition-all duration-300 group relative overflow-hidden whitespace-nowrap",
                                    {
                                        "bg-cyan/10 text-cyan border border-cyan/30 shadow-[0_0_15px_rgba(0,240,255,0.2)]": isActive,
                                        "text-gray-400 hover:text-white hover:bg-white/5 hover:border hover:border-white/10": !isActive
                                    }
                                )}
                            >
                                <item.icon className={classNames("w-6 h-6 flex-shrink-0", { "text-cyan drop-shadow-[0_0_5px_rgba(0,240,255,0.8)]": isActive })} />
                                <span className="font-orbitron tracking-widest text-sm flex-1 text-left">{item.label}</span>

                                {isActive && (
                                    <div className="absolute right-0 top-0 bottom-0 w-1 bg-cyan shadow-[0_0_10px_#00f0ff]" />
                                )}
                            </button>
                        );
                    })}
                </nav>

                <div className="p-4 border-t border-cyan/20 bg-black/40 mt-auto">
                    <div className="flex items-center justify-between text-xs font-mono text-gray-500 mb-2">
                        <span>CPU LOAD</span>
                        <span className="text-cyan">12%</span>
                    </div>
                    <div className="w-full bg-gray-800 h-1 rounded-full overflow-hidden">
                        <div className="bg-cyan w-[12%] h-full" />
                    </div>
                    <div className="flex items-center justify-between text-xs font-mono text-gray-500 mt-2 mb-2">
                        <span>MEMORY</span>
                        <span className="text-magenta">45%</span>
                    </div>
                    <div className="w-full bg-gray-800 h-1 rounded-full overflow-hidden">
                        <div className="bg-magenta w-[45%] h-full" />
                    </div>
                </div>
            </div>
        </>
    );
});

export default Sidebar;
