import { StatusBar } from 'expo-status-bar';
import { useEffect, useCallback } from 'react';
import { ActivityIndicator, Text, View } from 'react-native';
import * as SplashScreen from 'expo-splash-screen';
import {
  useFonts,
  CormorantGaramond_300Light,
  CormorantGaramond_400Regular,
  CormorantGaramond_500Medium,
  CormorantGaramond_400Regular_Italic,
} from '@expo-google-fonts/cormorant-garamond';
import { Inter_400Regular, Inter_500Medium, Inter_700Bold } from '@expo-google-fonts/inter';
import { JetBrainsMono_400Regular } from '@expo-google-fonts/jetbrains-mono';
import './global.css';
import { AuthProvider, useAuth } from './hooks/useAuth';
import { NavigationProvider, useNavigation } from './hooks/useNavigation';
import { api } from './services/api';
import ChatGoalCreateScreen from './screens/ChatGoalCreateScreen';
import DashboardScreen from './screens/DashboardScreen';
import GoalDetailScreen from './screens/GoalDetailScreen';
import HomeScreen from './screens/HomeScreen';
import LoginScreen from './screens/LoginScreen';
import NotificationListScreen from './screens/NotificationListScreen';
import ProofSubmissionScreen from './screens/ProofSubmissionScreen';
import ApiEndpointSubmissionScreen from './screens/ApiEndpointSubmissionScreen';
import DevSandboxSubmissionScreen from './screens/DevSandboxSubmissionScreen';

SplashScreen.preventAutoHideAsync();

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
      <View className="flex-1 items-center justify-center bg-codex-bg">
        <ActivityIndicator size="large" color="#8A2A1C" />
        <Text className="mt-4 font-sans text-sm text-codex-muted">Loading...</Text>
      </View>
    );
  }

  if (!isAuthenticated) {
    return <LoginScreen />;
  }

  if (currentScreen.name === 'dashboard') {
    return <DashboardScreen />;
  }

  if (currentScreen.name === 'chat-goal-create') {
    return <ChatGoalCreateScreen />;
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

  if (currentScreen.name === 'dev-sandbox-proof-submission') {
    return <DevSandboxSubmissionScreen goalId={currentScreen.goalId} />;
  }

  if (currentScreen.name === 'notifications') {
    return <NotificationListScreen />;
  }

  return <HomeScreen />;
}

export default function App() {
  const [fontsLoaded] = useFonts({
    CormorantGaramond_300Light,
    CormorantGaramond_400Regular,
    CormorantGaramond_500Medium,
    CormorantGaramond_400Regular_Italic,
    Inter_400Regular,
    Inter_500Medium,
    Inter_700Bold,
    JetBrainsMono_400Regular,
  });

  const onLayoutRootView = useCallback(async () => {
    if (fontsLoaded) {
      await SplashScreen.hideAsync();
    }
  }, [fontsLoaded]);

  if (!fontsLoaded) {
    return null;
  }

  return (
    <View className="flex-1 bg-codex-bg" onLayout={onLayoutRootView}>
      <AuthProvider>
        <NavigationProvider>
          <AppContent />
          <StatusBar style="dark" />
        </NavigationProvider>
      </AuthProvider>
    </View>
  );
}
