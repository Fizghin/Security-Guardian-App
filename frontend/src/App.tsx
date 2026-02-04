import Dashboard from './components/Dashboard';
import ErrorBoundary from './components/ErrorBoundary';
import { useEffect } from 'react';

function App() {
  useEffect(() => {
    document.title = "GUARDIAN ANGEL | AI Security Interphase";
  }, []);

  return (
    <ErrorBoundary>
      <Dashboard />
    </ErrorBoundary>
  );
}

export default App
