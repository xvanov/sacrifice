import { render, fireEvent, act } from '@testing-library/react-native';
import CameraCapture from '../../components/CameraCapture';
import { mockCamera } from '../../__mocks__/expo-camera';

// ---------------------------------------------------------------------------
// Alias mock controls to short names for readability
// ---------------------------------------------------------------------------
const mockRequestPermissions = mockCamera.requestPermissions;
const mockGetPermissions = mockCamera.getPermissions;
const mockRecordAsync = mockCamera.recordAsync;
const mockStopRecording = mockCamera.stopRecording;

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
    mockRecordAsync.mockReset();
    mockStopRecording.mockReset();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  // -- Permission denied state ------------------------------------------------

  describe('permission denied state', () => {
    it('renders camera-access-required message when permissions are denied', () => {
      mockGetPermissions.mockReturnValue(deniedPermissions());

      const screen = render(<CameraCapture />);

      expect(screen.getByText('Camera access is required to submit this proof')).toBeTruthy();
    });

    it('renders Open settings link when permissions are denied', () => {
      mockGetPermissions.mockReturnValue(deniedPermissions());

      const screen = render(<CameraCapture />);

      expect(screen.getByText('Open settings')).toBeTruthy();
    });

    it('renders Cancel link that calls onCancel when permissions are denied', () => {
      mockGetPermissions.mockReturnValue(deniedPermissions());
      const onCancel = jest.fn();

      const screen = render(<CameraCapture onCancel={onCancel} />);

      fireEvent.press(screen.getByText('Cancel'));
      expect(onCancel).toHaveBeenCalledTimes(1);
    });

    it('does not crash when permissions are permanently denied', () => {
      mockGetPermissions.mockReturnValue(permanentlyDeniedPermissions());

      const screen = render(<CameraCapture />);

      expect(screen.getByText('Camera access is required to submit this proof')).toBeTruthy();
      expect(screen.getByText('Open settings')).toBeTruthy();
    });
  });

  // -- Ready / preview state --------------------------------------------------

  describe('ready state', () => {
    it('renders Start recording button when permissions are granted', () => {
      mockGetPermissions.mockReturnValue(grantedPermissions());

      const screen = render(<CameraCapture />);

      expect(screen.getByText('Start recording')).toBeTruthy();
    });

    it('requests permissions on mount if not already granted', () => {
      mockGetPermissions.mockReturnValue(null);

      render(<CameraCapture />);

      expect(mockRequestPermissions).toHaveBeenCalled();
    });
  });

  // -- Recording state --------------------------------------------------------

  describe('recording state', () => {
    it('toggles to Stop recording button after Start recording is pressed', async () => {
      mockGetPermissions.mockReturnValue(grantedPermissions());

      const screen = render(<CameraCapture />);

      fireEvent.press(screen.getByText('Start recording'));

      await act(() => {
        jest.advanceTimersByTime(100);
      });

      expect(screen.getByText('Stop recording')).toBeTruthy();
    });

    it('renders an elapsed-time indicator while recording', async () => {
      mockGetPermissions.mockReturnValue(grantedPermissions());

      const screen = render(<CameraCapture />);

      fireEvent.press(screen.getByText('Start recording'));

      await act(() => {
        jest.advanceTimersByTime(3000);
      });

      // After 3 seconds of recording, an elapsed-time indicator should display
      expect(screen.getByText(/00:0[0-9]/)).toBeTruthy();
    });
  });

  // -- Auto-stop on maxDurationSeconds ----------------------------------------

  describe('auto-stop on maxDurationSeconds', () => {
    it('auto-stops recording when maxDurationSeconds is reached', async () => {
      mockGetPermissions.mockReturnValue(grantedPermissions());
      mockStopRecording.mockResolvedValue({ uri: 'file:///tmp/video.mp4' });

      const screen = render(<CameraCapture maxDurationSeconds={5} />);

      fireEvent.press(screen.getByText('Start recording'));

      // Advance past the max duration
      await act(() => {
        jest.advanceTimersByTime(5500);
      });

      // Should have auto-stopped — Retake and Use this video buttons appear
      expect(screen.getByText('Retake')).toBeTruthy();
      expect(screen.getByText('Use this video')).toBeTruthy();
    });
  });

  // -- Post-capture state (Retake / Use this video) ---------------------------

  describe('post-capture state', () => {
    async function captureVideo(screen: ReturnType<typeof render>) {
      mockGetPermissions.mockReturnValue(grantedPermissions());
      mockStopRecording.mockResolvedValue({ uri: 'file:///tmp/video.mp4' });

      fireEvent.press(screen.getByText('Start recording'));

      await act(() => {
        jest.advanceTimersByTime(500);
      });

      fireEvent.press(screen.getByText('Stop recording'));
    }

    it('shows Retake and Use this video choices after recording stops', async () => {
      mockGetPermissions.mockReturnValue(grantedPermissions());

      const screen = render(<CameraCapture />);

      fireEvent.press(screen.getByText('Start recording'));

      await act(() => {
        jest.advanceTimersByTime(500);
      });

      // Simulate stop recording completing
      fireEvent.press(screen.getByText('Stop recording'));

      await act(() => {
        jest.advanceTimersByTime(100);
      });

      expect(screen.getByText('Retake')).toBeTruthy();
      expect(screen.getByText('Use this video')).toBeTruthy();
    });

    it('resets to preview state when Retake is pressed', async () => {
      mockGetPermissions.mockReturnValue(grantedPermissions());
      mockStopRecording.mockResolvedValue({ uri: 'file:///tmp/video.mp4' });

      const screen = render(<CameraCapture />);

      // Start then stop recording
      fireEvent.press(screen.getByText('Start recording'));
      await act(() => {
        jest.advanceTimersByTime(200);
      });
      fireEvent.press(screen.getByText('Stop recording'));
      await act(() => {
        jest.advanceTimersByTime(100);
      });

      // Press Retake
      fireEvent.press(screen.getByText('Retake'));
      await act(() => {
        jest.advanceTimersByTime(100);
      });

      // Should be back in ready state with Start recording
      expect(screen.getByText('Start recording')).toBeTruthy();
      expect(screen.queryByText('Retake')).toBeNull();
      expect(screen.queryByText('Use this video')).toBeNull();
    });

    it('calls onCaptured with the video asset when Use this video is pressed', async () => {
      mockGetPermissions.mockReturnValue(grantedPermissions());
      const mockAsset = { uri: 'file:///tmp/video.mp4', duration: 2.3 };
      mockStopRecording.mockResolvedValue(mockAsset);
      const onCaptured = jest.fn();

      const screen = render(<CameraCapture onCaptured={onCaptured} />);

      fireEvent.press(screen.getByText('Start recording'));
      await act(() => {
        jest.advanceTimersByTime(200);
      });
      fireEvent.press(screen.getByText('Stop recording'));
      await act(() => {
        jest.advanceTimersByTime(100);
      });

      fireEvent.press(screen.getByText('Use this video'));

      await act(() => {
        jest.advanceTimersByTime(100);
      });

      expect(onCaptured).toHaveBeenCalledWith(mockAsset);
    });
  });
});