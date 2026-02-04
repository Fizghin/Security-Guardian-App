import React, { useEffect, useState } from 'react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import SciFiCard from './SciFiCard';
import { api } from '../services/api';
import type { SecurityEvent } from '../services/api';

interface AnalyticsPanelProps {
    stats?: any;
}

const AnalyticsPanel: React.FC<AnalyticsPanelProps> = ({ stats }) => {
    const [chartData, setChartData] = useState<any[]>([]);

    useEffect(() => {
        const prepareChartData = async () => {
            try {
                const events: SecurityEvent[] = await api.getEvents(100);
                // Simple grouping by hour
                const hourBuckets: { [key: string]: number } = {};

                // Initialize last 6 periods (every 4 hours)
                const now = new Date();
                for (let i = 0; i < 7; i++) {
                    const d = new Date(now.getTime() - (i * 4 * 60 * 60 * 1000));
                    const timeStr = `${d.getHours().toString().padStart(2, '0')}:00`;
                    hourBuckets[timeStr] = 0;
                }

                events.forEach(e => {
                    const date = new Date(e.timestamp);
                    const hour = date.getHours();
                    // Snap to 4-hour buckets for UI similarity to mock
                    const bucketHour = Math.floor(hour / 4) * 4;
                    const timeStr = `${bucketHour.toString().padStart(2, '0')}:00`;
                    if (hourBuckets[timeStr] !== undefined) {
                        hourBuckets[timeStr]++;
                    }
                });

                const formatted = Object.entries(hourBuckets)
                    .map(([time, count]) => ({ time, detections: count }))
                    .sort((a, b) => a.time.localeCompare(b.time));

                setChartData(formatted);
            } catch (err) {
                console.error("Analytics aggregation error:", err);
            }
        };

        prepareChartData();
        const interval = setInterval(prepareChartData, 10000); // Higher interval for analytics
        return () => clearInterval(interval);
    }, []);

    return (
        <SciFiCard title="SYSTEM ANALYTICS" className="h-full flex flex-col" color="electric">
            <div className="flex-1 min-h-[200px] mb-4">
                <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={chartData}>
                        <defs>
                            <linearGradient id="colorDetections" x1="0" y1="0" x2="0" y2="1">
                                <stop offset="5%" stopColor="#0080ff" stopOpacity={0.8} />
                                <stop offset="95%" stopColor="#0080ff" stopOpacity={0} />
                            </linearGradient>
                        </defs>
                        <XAxis dataKey="time" stroke="#0080ff" tick={{ fontSize: 10 }} />
                        <YAxis stroke="#0080ff" tick={{ fontSize: 10 }} />
                        <CartesianGrid strokeDasharray="3 3" stroke="#0080ff" opacity={0.1} />
                        <Tooltip
                            contentStyle={{ backgroundColor: 'rgba(5,5,16,0.9)', borderColor: '#0080ff' }}
                            itemStyle={{ color: '#00f0ff' }}
                        />
                        <Area type="monotone" dataKey="detections" stroke="#0080ff" fillOpacity={1} fill="url(#colorDetections)" />
                    </AreaChart>
                </ResponsiveContainer>
            </div>

            <div className="grid grid-cols-3 gap-2 text-center">
                <div className="bg-electric/10 p-2 rounded border border-electric/30">
                    <div className="text-2xl font-bold font-mono text-electric">{stats?.total_events ?? 0}</div>
                    <div className="text-[10px] text-electric/70 uppercase">Total Events</div>
                </div>
                <div className="bg-electric/10 p-2 rounded border border-electric/30">
                    <div className="text-2xl font-bold font-mono text-white">{stats?.active_threats ?? 0}</div>
                    <div className="text-[10px] text-electric/70 uppercase">Active Threads</div>
                </div>
                <div className="bg-magenta/10 p-2 rounded border border-magenta/30">
                    <div className="text-2xl font-bold font-mono text-magenta">{(stats?.threat_level ?? 0) > 0 ? 'HIGH' : 'SAFE'}</div>
                    <div className="text-[10px] text-magenta/70 uppercase">Neural Status</div>
                </div>
            </div>
        </SciFiCard>
    );
};

export default AnalyticsPanel;
