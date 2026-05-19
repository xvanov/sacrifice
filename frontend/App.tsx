import { StatusBar } from 'expo-status-bar';
import { useEffect } from 'react';
import { ActivityIndicator, Text, View } from 'react-native';
import './global.css';
import { AuthProvider, useAuth } from './hooks/useAuth';
import { api } from './services/api';
import HomeScreen from './screens/HomeScreen';
import LoginScreen from './screens/LoginScreen';

function AppContent() {
  const { isAuthenticated, isLoading } = useAuth();

  useEffect(() => {
    api.health().then((res) => {
      if (res.data) {
        console.log('API health:', res.data);
      } else {
        console.error('API health check failed:', res.error);
      }
    });
  }, []);

  if (isLoading) {
    return (
      <View className="flex-1 items-center justify-center bg-white">
        <ActivityIndicator size="large" color="#4F46E5" />
        <Text className="mt-4 text-sm text-gray-500">Loading...</Text>
      </View>
    );
  }

  if (!isAuthenticated) {
    return <LoginScreen />;
  }

  return <HomeScreen />;
}

export default function App() {
  return (
    <AuthProvider>
      <AppContent />
      <StatusBar style="auto" />
    </AuthProvider>
  );
}
