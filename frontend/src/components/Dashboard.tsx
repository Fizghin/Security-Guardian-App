import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import SciFiCard from './SciFiCard';
import AnalyticsPanel from './AnalyticsPanel';
import EventTimeline from './EventTimeline';
import ParticleBackground from './ParticleBackground';
import Sidebar from './Sidebar';
import Archives from './Archives';
import SystemConfig from './SystemConfig';
import Header from './Header';
import SurveillanceView from './SurveillanceView';

import { useSystemStats } from '../hooks/useSystemStats';

const Dashboard: React.FC = () => {
    const [activeView, setActiveView] = useState('surveillance');
    const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
    const { stats } = useSystemStats(2000);

    const renderContent = () => {
        switch (activeView) {
            case 'surveillance':
                return <SurveillanceView stats={stats} />;
            case 'analytics':
                return (
                    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                        <div className="lg:col-span-2">
                            <AnalyticsPanel stats={stats} />
                        </div>
                        <div>
                            <EventTimeline />
                        </div>
                    </div>
                );
            case 'archive':
                return <Archives />;
            case 'config':
                return <SystemConfig />;
            case 'reports':
                return (
                    <SciFiCard title="SECURITY REPORTS" className="h-full" color="cyan">
                        <div className="text-center text-gray-400 mt-20 font-mono">
                            GENERATING WEEKLY SUMMARY...
                            <br /><br />
                            <span className="text-cyan animate-pulse">PROCESSING DATA STREAMS [||||||||||] 100%</span>
                        </div>
                    </SciFiCard>
                );
            default:
                return <SurveillanceView stats={stats} />;
        }
    };

    return (
        <div className="h-screen w-screen bg-dark-bg text-cyan font-rajdhani overflow-hidden flex selection:bg-cyan selection:text-black relative">
            <ParticleBackground />
            <div className="scanlines" />

            <Sidebar
                activeView={activeView}
                onNavigate={(view) => {
                    setActiveView(view);
                    setIsMobileMenuOpen(false);
                }}
                isOpen={isMobileMenuOpen}
                onClose={() => setIsMobileMenuOpen(false)}
            />

            <main className="flex-1 flex flex-col h-full relative z-10 overflow-hidden">
                <Header
                    activeView={activeView}
                    onMenuClick={() => setIsMobileMenuOpen(true)}
                />

                <div className="p-6 flex-1 overflow-y-auto relative custom-scrollbar">
                    <AnimatePresence mode="wait">
                        <motion.div
                            key={activeView}
                            initial={{ opacity: 0, x: 10 }}
                            animate={{ opacity: 1, x: 0 }}
                            exit={{ opacity: 0, x: -10 }}
                            transition={{ duration: 0.2 }}
                            className="h-full"
                        >
                            {renderContent()}
                        </motion.div>
                    </AnimatePresence>
                </div>
            </main>
        </div>
    );
};

export default Dashboard;
