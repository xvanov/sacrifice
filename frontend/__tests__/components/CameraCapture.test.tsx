import { render, fireEvent, act } from '@testing-library/react-native';
import CameraCapture from '../../components/CameraCapture';
import { mockCamera } from '../../__mocks__/expo-camera';

// ---------------------------------------------------------------------------
// Alias mock controls to short names for readability
// ---------------------------------------------------------------------------
const mockRequestPermissions = mockCamera.requestPermissions;
const mockGetPermissions = mockCamera.getPermissions;
const mockRequestMicrophonePermissions = mockCamera.requestMicrophonePermissions;
const mockGetMicrophonePermissions = mockCamera.getMicrophonePermissions;
const mockRecord = mockCamera.record;
const mockRecordAsync = mockCamera.recordAsync;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function grantedPermissions() {
  return { granted: true, canAskAgain: true, expires: 'never' };
}

function deniedPermissions() {
  return { granted: false, canAskAgain: true, expires: 'never' };
}

function permanentlyDeniedPermissions() {
  return { granted: false, canAskAgain: false, expires: 'never' };
}

// ---------------------------------------------------------------------------
describe('CameraCapture', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    mockRequestPermissions.mockReset();
    mockGetPermissions.mockReset();
    mockRequestMicrophonePermissions.mockReset();
    mockGetMicrophonePermissions.mockReset();
    mockRecord.mockReset();
    mockRecordAsync.mockReset();
    mockCamera.stopRecording.mockReset();

    mockGetPermissions.mockReturnValue(grantedPermissions());
    mockGetMicrophonePermissions.mockReturnValue(grantedPermissions());
    mockRequestPermissions.mockResolvedValue(grantedPermissions());
    mockRequestMicrophonePermissions.mockResolvedValue(grantedPermissions());
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  // -- Permission ------------------------------------------------------------

  describe('permission state', () => {
    it('requests camera and microphone permissions on mount if not already granted', () => {
      mockGetPermissions.mockReturnValue(null);
      mockGetMicrophonePermissions.mockReturnValue(null);
      mockRequestPermissions.mockReturnValue(new Promise(() => undefined));
      mockRequestMicrophonePermissions.mockReturnValue(new Promise(() => undefined));

      render(<CameraCapture />);

      expect(mockRequestPermissions).toHaveBeenCalledTimes(1);
      expect(mockRequestMicrophonePermissions).toHaveBeenCalledTimes(1);
    });

    it('renders denied-state with message and all required controls', () => {
      mockGetPermissions.mockReturnValue(deniedPermissions());

      const screen = render(<CameraCapture onCancel={jest.fn()} />);

      expect(screen.getByText('Camera access is required to submit this proof')).toBeTruthy();
      expect(screen.getByText('Open settings')).toBeTruthy();
      expect(screen.getByText('Cancel')).toBeTruthy();
    });

    it('renders denied-state when microphone permission is denied', () => {
      mockGetMicrophonePermissions.mockReturnValue(deniedPermissions());

      const screen = render(<CameraCapture onCancel={jest.fn()} />);

      expect(screen.getByText('Camera access is required to submit this proof')).toBeTruthy();
      expect(screen.getByText('Open settings')).toBeTruthy();
      expect(screen.getByText('Cancel')).toBeTruthy();
    });

    it('does not render denied-state Cancel when onCancel is omitted', () => {
      mockGetPermissions.mockReturnValue(deniedPermissions());

      const screen = render(<CameraCapture />);

      expect(screen.queryByText('Cancel')).toBeNull();
    });

    it('calls onCancel when Cancel is pressed in denied state', () => {
      mockGetPermissions.mockReturnValue(deniedPermissions());
      const onCancel = jest.fn();

      const screen = render(<CameraCapture onCancel={onCancel} />);
      fireEvent.press(screen.getByText('Cancel'));

      expect(onCancel).toHaveBeenCalledTimes(1);
    });

    it('shows only Open settings when permissions are permanently denied (no Cancel)', () => {
      mockGetPermissions.mockReturnValue(permanentlyDeniedPermissions());

      const screen = render(<CameraCapture />);

      // Permanently denied: message and settings link, but no Cancel
      expect(screen.getByText('Camera access is required to submit this proof')).toBeTruthy();
      expect(screen.getByText('Open settings')).toBeTruthy();
      expect(screen.queryByText('Cancel')).toBeNull();
    });
  });

  // -- Ready state -----------------------------------------------------------

  describe('ready state', () => {
    it('renders camera preview with Start recording button and no post-capture controls', () => {
      mockGetPermissions.mockReturnValue(grantedPermissions());

      const screen = render(<CameraCapture />);

      expect(screen.getByTestId('camera-preview')).toBeTruthy();
      expect(screen.getAllByText('Start recording')).toHaveLength(1);
      expect(screen.queryByText('Stop recording')).toBeNull();
      expect(screen.queryByText('Retake')).toBeNull();
      expect(screen.queryByText('Use this video')).toBeNull();
    });
  });

  // -- Recording state -------------------------------------------------------

  describe('recording state', () => {
    it('starts recording through CameraView recordAsync with maxDuration when provided', async () => {
      mockGetPermissions.mockReturnValue(grantedPermissions());
      mockRecordAsync.mockReturnValue(new Promise<{ uri: string }>(() => undefined));

      const screen = render(<CameraCapture maxDurationSeconds={12} />);

      fireEvent.press(screen.getByText('Start recording'));

      await act(() => {
        jest.advanceTimersByTime(100);
      });

      expect(mockRecordAsync).toHaveBeenCalledWith({ maxDuration: 12 });
      expect(mockRecord).not.toHaveBeenCalled();
    });

    it('toggles to Stop recording button and shows elapsed time after start', async () => {
      mockGetPermissions.mockReturnValue(grantedPermissions());
      mockRecordAsync.mockReturnValue(new Promise<{ uri: string }>(() => undefined));

      const screen = render(<CameraCapture />);

      // Initial state: start button visible with 00:00 timer (or no timer)
      fireEvent.press(screen.getByText('Start recording'));

      await act(() => {
        jest.advanceTimersByTime(100);
      });

      // Now recording: Stop button + elapsed timer
      expect(screen.getByText('Stop recording')).toBeTruthy();
      // Elapsed timer shows exactly 00:00 at the start of recording
      expect(screen.getByText('00:00')).toBeTruthy();

      // After 3 more seconds, timer must tick to 00:03
      await act(() => {
        jest.advanceTimersByTime(3000);
      });

      expect(screen.getByText('00:03')).toBeTruthy();
      expect(screen.queryByText('00:00')).toBeNull();
    });
  });

  // -- Auto-stop -------------------------------------------------------------

  describe('auto-stop on maxDurationSeconds', () => {
    it('auto-stops recording when maxDurationSeconds is reached, showing post-capture controls', async () => {
      mockGetPermissions.mockReturnValue(grantedPermissions());
      const recordedAsset = { uri: 'file:///tmp/video.mp4' };
      let resolveRecording: (value: { uri: string }) => void;
      const recordPromise = new Promise<{ uri: string }>((resolve) => {
        resolveRecording = resolve;
      });
      mockRecordAsync.mockReturnValue(recordPromise);
      // stopRecording (called by auto-stop effect) resolves recordAsync
      mockCamera.stopRecording.mockImplementation(() => {
        resolveRecording(recordedAsset);
      });

      const screen = render(<CameraCapture maxDurationSeconds={5} />);

      fireEvent.press(screen.getByText('Start recording'));

      // Advance past the max duration — the component's auto-stop effect
      // calls stopRecording which resolves the pending record() promise
      // and moves to 'preview' status
      await act(async () => {
        jest.advanceTimersByTime(5500);
        await Promise.resolve();
      });

      // Post-capture state: Retake + Use this video visible, recording UI gone
      expect(screen.getByText('Retake')).toBeTruthy();
      expect(screen.getByText('Use this video')).toBeTruthy();
      expect(screen.queryByText('Stop recording')).toBeNull();
      expect(screen.queryByText('Start recording')).toBeNull();
    });
  });

  // -- Post-capture state ----------------------------------------------------

  describe('post-capture state', () => {
    /**
     * Helper that wires mockRecord (called by the component's startRecording)
     * and mockCamera.stopRecording to mimic real Expo Camera behaviour:
     * record() returns a Promise that resolves with the asset only when
     * stopRecording is called.
     */
    function wireRealisticRecording(asset: { uri: string }) {
      let resolveRecording: (value: { uri: string }) => void;
      const recordPromise = new Promise<{ uri: string }>((resolve) => {
        resolveRecording = resolve;
      });
      mockRecordAsync.mockReturnValue(recordPromise);
      mockCamera.stopRecording.mockImplementation(() => {
        resolveRecording(asset);
      });
    }

    it('shows Retake and Use this video with captured asset after recording stops', async () => {
      mockGetPermissions.mockReturnValue(grantedPermissions());
      const recordedAsset = { uri: 'file:///tmp/recorded.mp4' };
      wireRealisticRecording(recordedAsset);

      const screen = render(<CameraCapture />);

      // Start recording — recordAsync promise is now pending
      fireEvent.press(screen.getByText('Start recording'));

      await act(() => {
        jest.advanceTimersByTime(500);
      });

      // Manually stop — this resolves recordAsync with the asset
      await act(async () => {
        fireEvent.press(screen.getByText('Stop recording'));
        await Promise.resolve();
      });

      // Post-capture: preview controls visible with the captured asset
      expect(screen.getByText('Retake')).toBeTruthy();
      expect(screen.getByText('Use this video')).toBeTruthy();
      expect(screen.queryByText('Start recording')).toBeNull();
      expect(screen.queryByText('Stop recording')).toBeNull();
    });

    it('shows confirm actions only after the recording promise resolves with an asset', async () => {
      mockGetPermissions.mockReturnValue(grantedPermissions());
      const recordedAsset = { uri: 'file:///tmp/delayed.mp4' };
      let resolveRecording: (value: { uri: string }) => void = () => undefined;
      const recordPromise = new Promise<{ uri: string }>((resolve) => {
        resolveRecording = resolve;
      });
      mockRecordAsync.mockReturnValue(recordPromise);
      mockCamera.stopRecording.mockImplementation(() => undefined);

      const screen = render(<CameraCapture />);

      fireEvent.press(screen.getByText('Start recording'));

      await act(async () => {
        fireEvent.press(screen.getByText('Stop recording'));
        await Promise.resolve();
      });

      expect(screen.queryByText('Retake')).toBeNull();
      expect(screen.queryByText('Use this video')).toBeNull();

      await act(async () => {
        resolveRecording(recordedAsset);
        await Promise.resolve();
      });

      expect(screen.getByText('Retake')).toBeTruthy();
      expect(screen.getByText('Use this video')).toBeTruthy();
    });


    it('returns to ready state when Retake is pressed', async () => {
      mockGetPermissions.mockReturnValue(grantedPermissions());
      const recordedAsset = { uri: 'file:///tmp/recorded.mp4' };
      wireRealisticRecording(recordedAsset);

      const screen = render(<CameraCapture />);

      // Drive through a full record-stop cycle
      fireEvent.press(screen.getByText('Start recording'));
      await act(() => {
        jest.advanceTimersByTime(200);
      });
      await act(async () => {
        fireEvent.press(screen.getByText('Stop recording'));
        await Promise.resolve();
      });

      // Press Retake
      fireEvent.press(screen.getByText('Retake'));
      await act(() => {
        jest.advanceTimersByTime(100);
      });

      // Back in ready state
      expect(screen.getByText('Start recording')).toBeTruthy();
      expect(screen.queryByText('Retake')).toBeNull();
      expect(screen.queryByText('Use this video')).toBeNull();
      expect(screen.queryByText('Stop recording')).toBeNull();
    });

    it('calls onCaptured with the asset when Use this video is pressed', async () => {
      mockGetPermissions.mockReturnValue(grantedPermissions());
      const mockAsset = { uri: 'file:///tmp/recorded.mp4' };
      wireRealisticRecording(mockAsset);
      const onCaptured = jest.fn();

      const screen = render(<CameraCapture onCaptured={onCaptured} />);

      // Drive through a full record-stop cycle
      fireEvent.press(screen.getByText('Start recording'));
      await act(() => {
        jest.advanceTimersByTime(200);
      });
      await act(async () => {
        fireEvent.press(screen.getByText('Stop recording'));
        await Promise.resolve();
      });

      fireEvent.press(screen.getByText('Use this video'));

      await act(() => {
        jest.advanceTimersByTime(100);
      });

      expect(onCaptured).toHaveBeenCalledWith(mockAsset);
    });
  });
});