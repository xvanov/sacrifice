import { useCallback, useEffect, useRef, useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { CameraView } from 'expo-camera';
import { useCameraPermissions, useMicrophonePermissions } from 'expo-camera';
import { openSettings } from 'expo-linking';
import { useNavigation } from '../hooks/useNavigation';

interface CameraCapturedAsset {
  uri: string;
}

interface Props {
  onCaptured: (asset: CameraCapturedAsset) => void;
  maxDurationSeconds?: number;
}

type CaptureState = 'loading' | 'denied' | 'ready' | 'recording' | 'review';

export default function CameraCapture({ onCaptured, maxDurationSeconds }: Props) {
  const { goBack } = useNavigation();
  const cameraRef = useRef<CameraView>(null);
  const [cameraPerm, requestCameraPerm] = useCameraPermissions();
  const [microphonePerm, requestMicrophonePerm] = useMicrophonePermissions();

  const [captureState, setCaptureState] = useState<CaptureState>('loading');
  const [elapsed, setElapsed] = useState(0);
  const [recordedUri, setRecordedUri] = useState<string | null>(null);
  const elapsedRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Request permissions on mount if not determined.
  useEffect(() => {
    if (!cameraPerm || !microphonePerm) return;

    const requestIfNeeded = async () => {
      let cam = cameraPerm;
      let mic = microphonePerm;

      if (!cam.granted && cam.canAskAgain) {
        cam = await requestCameraPerm();
      }
      if (!mic.granted && mic.canAskAgain) {
        mic = await requestMicrophonePerm();
      }

      if (!cam.granted || !mic.granted) {
        setCaptureState('denied');
      } else {
        setCaptureState('ready');
      }
    };

    requestIfNeeded();
  }, [cameraPerm, microphonePerm, requestCameraPerm, requestMicrophonePerm]);

  // Cleanup interval on unmount.
  useEffect(() => {
    return () => {
      if (elapsedRef.current) clearInterval(elapsedRef.current);
    };
  }, []);

  const startRecording = useCallback(async () => {
    const cam = cameraRef.current;
    if (!cam) return;
    setElapsed(0);
    setCaptureState('recording');

    elapsedRef.current = setInterval(() => {
      setElapsed((prev) => prev + 1);
    }, 1000);

    try {
      const result = await cam.recordAsync(
        maxDurationSeconds ? { maxDuration: maxDurationSeconds } : undefined,
      );
      // recordAsync resolves when recording stops (via stopRecording or maxDuration).
      if (elapsedRef.current) clearInterval(elapsedRef.current);

      if (result?.uri) {
        setRecordedUri(result.uri);
        setCaptureState('review');
      } else {
        // Recording was aborted or produced no URI; go back to ready.
        setCaptureState('ready');
      }
    } catch {
      if (elapsedRef.current) clearInterval(elapsedRef.current);
      setCaptureState('ready');
    }
  }, [maxDurationSeconds]);

  const stopRecording = useCallback(() => {
    cameraRef.current?.stopRecording();
  }, []);

  const handleRetake = useCallback(() => {
    setRecordedUri(null);
    setElapsed(0);
    setCaptureState('ready');
  }, []);

  const handleUseVideo = useCallback(() => {
    if (recordedUri) {
      onCaptured({ uri: recordedUri });
    }
  }, [recordedUri, onCaptured]);

  const formatElapsed = (seconds: number): string => {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  };

  // --- Denied permission state (AC2) ---
  if (captureState === 'denied') {
    return (
      <View className="flex-1 items-center justify-center bg-codex-bg px-6">
        <Text className="mb-4 text-center font-sans text-lg text-codex-accent">
          Camera access is required to submit this proof
        </Text>
        <View className="flex-row gap-4">
          <Pressable
            onPress={openSettings}
            className="rounded-sm bg-codex-accent px-6 py-3"
          >
            <Text className="font-sans-medium text-base text-codex-surface">
              Open settings
            </Text>
          </Pressable>
          <Pressable
            onPress={goBack}
            className="rounded-sm border border-codex-border px-6 py-3"
          >
            <Text className="font-sans-medium text-base text-codex-text">
              Cancel
            </Text>
          </Pressable>
        </View>
      </View>
    );
  }

  // --- Loading state ---
  if (captureState === 'loading') {
    return (
      <View className="flex-1 items-center justify-center bg-codex-bg">
        <Text className="font-sans text-sm text-codex-muted">
          Requesting camera permissions...
        </Text>
      </View>
    );
  }

  // --- Review state (AC1: Retake / Use this video) ---
  if (captureState === 'review' && recordedUri) {
    return (
      <View className="flex-1 bg-codex-bg">
        {/* Video preview - on native this would be a Video player; we show the URI as a fallback */}
        <View className="flex-1 items-center justify-center px-6">
          <Text className="mb-4 font-sans text-sm text-codex-muted">
            Recording captured ({formatElapsed(elapsed)})
          </Text>
          <Text className="mb-8 font-mono text-xs text-codex-muted" numberOfLines={2}>
            {recordedUri}
          </Text>
          <View className="flex-row gap-4">
            <Pressable
              onPress={handleRetake}
              className="rounded-sm border border-codex-border px-6 py-3"
            >
              <Text className="font-sans-medium text-base text-codex-text">
                Retake
              </Text>
            </Pressable>
            <Pressable
              onPress={handleUseVideo}
              className="rounded-sm bg-codex-accent px-6 py-3"
            >
              <Text className="font-sans-medium text-base text-codex-surface">
                Use this video
              </Text>
            </Pressable>
          </View>
        </View>
      </View>
    );
  }

  // --- Ready or Recording state ---
  return (
    <View className="flex-1 bg-codex-bg">
      <CameraView
        ref={cameraRef}
        mode="video"
        className="flex-1"
        onCameraReady={() => {
          if (captureState === 'loading') setCaptureState('ready');
        }}
      />

      {/* Recording overlay */}
      <View className="absolute bottom-0 left-0 right-0 items-center pb-10">
        {captureState === 'recording' && (
          <View className="mb-4 rounded-sm bg-codex-accent px-4 py-1">
            <Text className="font-mono text-lg text-codex-surface">
              {formatElapsed(elapsed)}
            </Text>
          </View>
        )}

        {captureState === 'ready' && (
          <Pressable
            onPress={startRecording}
            className="rounded-full bg-codex-accent px-10 py-4"
          >
            <Text className="font-sans-medium text-base text-codex-surface">
              Start recording
            </Text>
          </Pressable>
        )}

        {captureState === 'recording' && (
          <Pressable
            onPress={stopRecording}
            className="rounded-full border-2 border-codex-accent bg-codex-bg px-10 py-4"
          >
            <Text className="font-sans-medium text-base text-codex-accent">
              Stop recording
            </Text>
          </Pressable>
        )}
      </View>
    </View>
  );
}