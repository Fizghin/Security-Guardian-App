import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.guardian.app',
  appName: 'Guardian Angel',
  webDir: 'dist',
  server: {
    cleartext: true
  }
};

export default config;
