"""GPIO Manager for status LEDs on Raspberry Pi"""
import logging
import asyncio
import platform

logger = logging.getLogger(__name__)

# Mock GPIO if not on Raspberry Pi or library missing
try:
    if platform.system() == "Linux" and platform.machine() in ["aarch64", "armv7l"]:
        import RPi.GPIO as GPIO
        HAS_GPIO = True
    else:
        raise ImportError("Not on Raspberry Pi")
except (ImportError, RuntimeError):
    HAS_GPIO = False
    class GPIO:
        BCM = 'BCM'
        OUT = 'OUT'
        HIGH = 'HIGH'
        LOW = 'LOW'
        @staticmethod
        def setmode(mode): pass
        @staticmethod
        def setup(pin, mode): pass
        @staticmethod
        def output(pin, state): pass
        @staticmethod
        def cleanup(): pass

class GPIOManager:
    """
    Manages Status LEDs.
    Default Config:
    - Red (Error): Pin 17
    - Green (Success/Idle): Pin 27
    - Blue (Busy/Scanning): Pin 22
    """
    
    def __init__(self, red_pin=17, green_pin=27, blue_pin=22):
        self.red_pin = red_pin
        self.green_pin = green_pin
        self.blue_pin = blue_pin
        self.current_task: asyncio.Task = None
        self._running = False

    async def initialize(self):
        """Setup GPIO pins"""
        if not HAS_GPIO:
            logger.debug("GPIO not available, skipping LED setup")
            return

        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.red_pin, GPIO.OUT)
            GPIO.setup(self.green_pin, GPIO.OUT)
            GPIO.setup(self.blue_pin, GPIO.OUT)
            self._turn_off_all()
            self._running = True
            logger.info("GPIO LEDs initialized")
        except Exception as e:
            logger.error(f"Failed to initialize GPIO: {e}")
            self._running = False

    def cleanup(self):
        """Cleanup GPIO"""
        if not HAS_GPIO or not self._running:
            return
        
        self._stop_current_pattern()
        try:
            GPIO.cleanup()
        except:
            pass

    def _turn_off_all(self):
        GPIO.output(self.red_pin, GPIO.LOW)
        GPIO.output(self.green_pin, GPIO.LOW)
        GPIO.output(self.blue_pin, GPIO.LOW)

    def _stop_current_pattern(self):
        if self.current_task and not self.current_task.done():
            self.current_task.cancel()

    async def update_status(self, status: str):
        """Update LED pattern based on status"""
        if not HAS_GPIO or not self._running:
            return

        self._stop_current_pattern()
        self._turn_off_all()
        
        # Determine pattern
        if status == 'idle':
            # Solid Green (dim? No PWM here, just solid)
            GPIO.output(self.green_pin, GPIO.HIGH)
            
        elif status == 'scanning' or status.startswith('scanning'):
            # Solid Blue
            GPIO.output(self.blue_pin, GPIO.HIGH)
            
        elif status == 'backing_up':
            # Blinking Green
            self.current_task = asyncio.create_task(self._blink(self.green_pin))
            
        elif status == 'completed':
            # Solid Green
            GPIO.output(self.green_pin, GPIO.HIGH)
            
        elif status == 'failed' or status == 'completed_with_errors':
            # Solid Red
            GPIO.output(self.red_pin, GPIO.HIGH)
            
        elif status == 'pending_approval':
            # Blinking Blue
            self.current_task = asyncio.create_task(self._blink(self.blue_pin))

    async def _blink(self, pin, interval=0.5):
        try:
            while True:
                GPIO.output(pin, GPIO.HIGH)
                await asyncio.sleep(interval)
                GPIO.output(pin, GPIO.LOW)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            GPIO.output(pin, GPIO.LOW)
