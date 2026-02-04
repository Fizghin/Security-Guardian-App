import { useState, useEffect } from 'react';
import { api } from '../services/api';
import type { SystemStats } from '../services/api';

export const useSystemStats = (pollInterval = 2000) => {
    const [stats, setStats] = useState<SystemStats>({
        total_events: 0,
        active_threats: 0,
        threat_level: 0,
        system_status: 'OFFLINE'
    });
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<Error | null>(null);

    useEffect(() => {
        let isMounted = true;

        const fetchStats = async () => {
            try {
                const data = await api.getStats();
                if (isMounted) {
                    setStats(data);
                    setLoading(false);
                }
            } catch (err) {
                if (isMounted) {
                    setError(err as Error);
                    setLoading(false);
                }
            }
        };

        fetchStats();
        const interval = setInterval(fetchStats, pollInterval);

        return () => {
            isMounted = false;
            clearInterval(interval);
        };
    }, [pollInterval]);

    return { stats, loading, error };
};
