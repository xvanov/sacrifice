import { render, fireEvent } from '@testing-library/react-native';
import { DatePickerField } from '../../components/DatePickerField';

// Regression tests for the bug where changing the calendar date reset the
// deadline to LOCAL MIDNIGHT, discarding the time picked separately. Combined
// with `toISOString()` that pushed same-day deadlines into the past, which the
// backend guard then rejected ("must be at least an hour in the future"), even
// though the user had chosen a valid future time.

function at(year: number, monthIndex: number, day: number, hour: number, minute = 0): Date {
  return new Date(year, monthIndex, day, hour, minute, 0, 0);
}

describe('DatePickerField preserves time-of-day', () => {
  it('keeps the existing time when the date is typed (YYYY-MM-DD)', () => {
    const onChange = jest.fn();
    // Current value: 6:00 AM local on 2026-07-23.
    const value = at(2026, 6, 23, 6, 0);
    const { getByTestId } = render(<DatePickerField value={value} onChange={onChange} />);

    fireEvent.changeText(getByTestId('deadline-date-input'), '2026-08-15');

    expect(onChange).toHaveBeenCalledTimes(1);
    const next: Date = onChange.mock.calls[0][0];
    // Date advanced...
    expect(next.getFullYear()).toBe(2026);
    expect(next.getMonth()).toBe(7); // August
    expect(next.getDate()).toBe(15);
    // ...but the 6:00 AM time-of-day is preserved (was being zeroed to midnight).
    expect(next.getHours()).toBe(6);
    expect(next.getMinutes()).toBe(0);
  });

  it('keeps the existing time when a calendar day is tapped', () => {
    const onChange = jest.fn();
    const value = at(2026, 6, 23, 6, 0);
    const { getByTestId, getByText } = render(
      <DatePickerField value={value} onChange={onChange} />,
    );

    // Open the calendar (nav month/year seed from `value` → July 2026).
    fireEvent(getByTestId('deadline-date-input'), 'focus');
    // Pick a different day in the same month.
    fireEvent.press(getByText('10'));

    expect(onChange).toHaveBeenCalled();
    const next: Date = onChange.mock.calls[onChange.mock.calls.length - 1][0];
    expect(next.getMonth()).toBe(6); // July (navMonth from value)
    expect(next.getDate()).toBe(10);
    // The time survives the day selection instead of resetting to midnight.
    expect(next.getHours()).toBe(6);
    expect(next.getMinutes()).toBe(0);
  });
});
