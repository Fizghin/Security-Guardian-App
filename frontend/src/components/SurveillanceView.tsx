import React from 'react';
import { motion } from 'framer-motion';
import VideoFeed from './VideoFeed';
import SciFiCard from './SciFiCard';
import EventTimeline from './EventTimeline';
import { api } from '../services/api';
import type { SystemStats } from '../services/api';

interface SurveillanceViewProps {
    stats: SystemStats;
}

const SurveillanceView: React.FC<SurveillanceViewProps> = ({ stats }) => {
    const handleSimulate = async () => {
        try {
            await api.simulatePerson();
            alert("SIMULATION STARTED: FAKE PERSON DETECTED FOR 10s");
        } catch (err) {
            console.error(err);
        }
    };

    const handleUpdateParams = async () => {
        try {
            await api.updateConfig({ intimidation: 75, humor: 30, persistence: 90 });
            alert("AI PROTOCOL PARAMETERS UPDATED SUCCESSFULLY.");
        } catch (err) {
            console.error(err);
        }
    };

    return (
        <div className="grid grid-cols-12 gap-6 min-h-full">
            {/* Video Feed & Main Controls */}
            <div className="col-span-12 lg:col-span-8 flex flex-col gap-6 h-full">
                <SciFiCard className="flex-1 p-0 overflow-hidden border-cyan/50 shadow-[0_0_30px_rgba(0,240,255,0.1)] relative">
                    <div className="absolute top-2 left-2 z-10 px-2 py-1 bg-black/60 rounded border border-cyan/30 text-[10px] text-cyan">
                        CAM_01 :: MAIN_ENTRY
                    </div>
                    <VideoFeed
                        lastAiMessage={stats.last_ai_message}
                        messageTime={stats.message_time}
                    />
                </SciFiCard>

                <div className="h-48 grid grid-cols-2 gap-4">
                    <SciFiCard title="QUICK STATS" className="h-full text-xs">
                        <div className="grid grid-cols-2 gap-4 mt-2">
                            <div>
                                <div className="text-gray-500">TODAY</div>
                                <div className="text-2xl text-white font-bold">{stats.total_events}</div>
                            </div>
                            <div>
                                <div className="text-gray-500">THREAT LEVEL</div>
                                <div className="text-2xl text-magenta font-bold">{stats.threat_level}</div>
                            </div>
                        </div>
                    </SciFiCard>
                    <SciFiCard title="ACTIVE THREATS" className="h-full" color={stats.active_threats > 0 ? "magenta" : "cyan"}>
                        <div className={`flex items-center justify-center h-full font-bold tracking-widest ${stats.active_threats > 0 ? "text-red-500 animate-pulse text-xl" : "text-green-500"}`}>
                            {stats.active_threats > 0 ? "INTRUDER DETECTED" : "NONE DETECTED"}
                        </div>
                    </SciFiCard>
                </div>
            </div>

            {/* Right Panel: Controls */}
            <div className="col-span-12 lg:col-span-4 flex flex-col gap-6 h-full">
                <SciFiCard title="AI PROTOCOLS" className="flex-none" color="electric">
                    <div className="space-y-6">
                        {[
                            { label: "INTIMIDATION", val: stats.ai_personality?.intimidation ?? 50, color: "bg-red-500", text: "text-red-500" },
                            { label: "HUMOR", val: stats.ai_personality?.humor ?? 20, color: "bg-yellow-500", text: "text-yellow-500" },
                            { label: "PERSISTENCE", val: stats.ai_personality?.persistence ?? 80, color: "bg-blue-500", text: "text-blue-500" }
                        ].map((stat) => (
                            <div key={stat.label}>
                                <div className="flex justify-between text-xs font-mono mb-2">
                                    <span className={stat.text}>{stat.label}</span>
                                    <span className="text-white">{stat.val}%</span>
                                </div>
                                <div className="h-2 bg-gray-900 rounded-full overflow-hidden border border-gray-700">
                                    <motion.div
                                        initial={{ width: 0 }}
                                        animate={{ width: `${stat.val}%` }}
                                        transition={{ duration: 1, delay: 0.5 }}
                                        className={`h-full ${stat.color} shadow-[0_0_10px_currentColor]`}
                                    />
                                </div>
                            </div>
                        ))}
                    </div>
                    <div className="mt-6 flex flex-col gap-2">
                        <button
                            className="btn-primary w-full text-xs"
                            onClick={handleUpdateParams}
                        >
                            UPDATE PARAMETERS
                        </button>
                        <button
                            className="btn-danger w-full text-xs mt-2 border border-red-500/50 bg-red-900/20 hover:bg-red-900/50 text-red-200"
                            onClick={handleSimulate}
                        >
                            SIMULATE PERSON
                        </button>
                    </div>
                </SciFiCard>

                <EventTimeline />
            </div>
        </div>
    );
};

export default SurveillanceView;
