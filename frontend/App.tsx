import { StatusBar } from 'expo-status-bar';
import { useEffect } from 'react';
import { Text, View } from 'react-native';
import './global.css';
import { api } from './services/api';

export default function App() {
  useEffect(() => {
    api.health().then((res) => {
      if (res.data) {
        console.log('API health:', res.data);
      } else {
        console.error('API health check failed:', res.error);
      }
    });
  }, []);

  return (
    <View className="flex-1 items-center justify-center bg-white">
      <Text className="text-3xl font-bold text-indigo-600">Sacrifice</Text>
      <StatusBar style="auto" />
    </View>
  );
}
