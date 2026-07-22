import { describe, it, expect, vi, afterEach } from 'vitest';
import { analyticsController } from '../../../src/lib/controller/analyticsController';
import { analyticsStore } from '../../../src/lib/stores/analytics-store';

afterEach(() => vi.restoreAllMocks());

describe('analyticsController', () => {
  describe('init', () => {
    it('calls analyticsStore.load with the given indexId', () => {
      const spy = vi.spyOn(analyticsStore, 'load').mockResolvedValue(undefined);
      analyticsController.init('idx-123');
      expect(spy).toHaveBeenCalledOnce();
      expect(spy).toHaveBeenCalledWith('idx-123');
    });
  });

  describe('changeIndex', () => {
    it('calls analyticsStore.load with the new indexId', () => {
      const spy = vi.spyOn(analyticsStore, 'load').mockResolvedValue(undefined);
      analyticsController.changeIndex('idx-456');
      expect(spy).toHaveBeenCalledWith('idx-456');
    });

    it('does nothing when indexId is an empty string', () => {
      const spy = vi.spyOn(analyticsStore, 'load').mockResolvedValue(undefined);
      analyticsController.changeIndex('');
      expect(spy).not.toHaveBeenCalled();
    });
  });

  describe('changeRange', () => {
    it('calls analyticsStore.setRange with the given range', () => {
      const spy = vi.spyOn(analyticsStore, 'setRange');
      analyticsController.changeRange('7d');
      expect(spy).toHaveBeenCalledWith('7d');
    });

    it('passes 30d range correctly', () => {
      const spy = vi.spyOn(analyticsStore, 'setRange');
      analyticsController.changeRange('30d');
      expect(spy).toHaveBeenCalledWith('30d');
    });

    it('passes 90d range correctly', () => {
      const spy = vi.spyOn(analyticsStore, 'setRange');
      analyticsController.changeRange('90d');
      expect(spy).toHaveBeenCalledWith('90d');
    });
  });

  describe('teardown', () => {
    it('calls analyticsStore.reset', () => {
      const spy = vi.spyOn(analyticsStore, 'reset');
      analyticsController.teardown();
      expect(spy).toHaveBeenCalledOnce();
    });
  });
});
