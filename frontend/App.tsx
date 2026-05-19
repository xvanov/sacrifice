import { StatusBar } from 'expo-status-bar';
import { useEffect } from 'react';
import { ActivityIndicator, Text, View } from 'react-native';
import './global.css';
import { AuthProvider, useAuth } from './hooks/useAuth';
import { NavigationProvider, useNavigation } from './hooks/useNavigation';
import { api } from './services/api';
import GoalCreateScreen from './screens/GoalCreateScreen';
import GoalDetailScreen from './screens/GoalDetailScreen';
import HomeScreen from './screens/HomeScreen';
import LoginScreen from './screens/LoginScreen';
import ProofSubmissionScreen from './screens/ProofSubmissionScreen';
import ApiEndpointSubmissionScreen from './screens/ApiEndpointSubmissionScreen';

function AppContent() {
  const { isAuthenticated, isLoading } = useAuth();
  const { currentScreen, navigate, goBack } = useNavigation();

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

  if (currentScreen.name === 'goal-create') {
    return <GoalCreateScreen />;
  }

  if (currentScreen.name === 'goal-detail') {
    return <GoalDetailScreen goalId={currentScreen.goalId} />;
  }

  if (currentScreen.name === 'proof-submission') {
    return <ProofSubmissionScreen goalId={currentScreen.goalId} />;
  }

  if (currentScreen.name === 'api-endpoint-proof-submission') {
    return <ApiEndpointSubmissionScreen goalId={currentScreen.goalId} />;
  }

  return <HomeScreen />;
}

export default function App() {
  return (
    <AuthProvider>
      <NavigationProvider>
        <AppContent />
        <StatusBar style="auto" />
      </NavigationProvider>
    </AuthProvider>
  );
}
