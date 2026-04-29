#
# BigTreeTech TFT35 bridge
#
# Original author: K. Hui (https://github.com/oldhui-uk/tftbridge)
#
# Enhancements in this fork:
#   - Race condition fix: tftSerial/klipperSerial snapshotted to local
#     variables before use so a disconnect on another thread can't null
#     them between the None-check and the write
#   - ok-prefix injection: bare temperature report lines from Klipper
#     lack an 'ok' prefix, which stalls the TFT command queue in
#     remote-host mode; this prepends 'ok' so the queue stays moving
#   - Print state monitoring: reactor timer polls print_stats and
#     virtual_sdcard and sends //action:print_start/pause/resume/
#     print_end/cancel plus Data Left / Time Left / Layer Left
#     notifications during Mainsail/Fluidd prints
#   - TFT_NOTIFY command: G-code command that writes
#     //action:notification directly to TFT serial, bypassing
#     klippy.serial (RESPOND cannot reach tftbridge in remote-host mode)
#   - Notification filtering: suppresses "pending gcode released" as a
#     TFT pop-up (still logged); shows "must home" as a dismissible
#     prompt dialog instead of a plain notification
#
import re
import serial
import threading
import logging

_TEMP_RE = re.compile(r'^[BT]\d*:')

_log = logging.getLogger('tftbridge')
_log.setLevel(logging.WARNING)
_fh = logging.FileHandler('/home/pi/tftbridge.log')
_fh.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
_log.addHandler(_fh)

class TftBridge:
	def __init__(self,config):
		self.printer = config.get_printer()
		#
		#get config
		#
		self.tftDevice = config.get('tft_device')
		self.tftBaud = config.getint('tft_baud')
		self.tftTimeout = config.getint('tft_timeout')
		self.klipperDevice = config.get('klipper_device')
		self.klipperBaud = config.getint('klipper_baud')
		self.klipperTimeout = config.getint('klipper_timeout')
		#
		#connections to TFT35 and Klipper serial ports
		#
		self.tftSerial = None
		self.klipperSerial = None
		#
		#event to signal stopping threads
		#
		self.stopEvent=threading.Event()
		#
		#last known print state for transition detection
		#
		self.last_print_state = 'standby'
		#
		#register event handlers
		#
		self.printer.register_event_handler("klippy:ready",self.handle_ready)
		self.printer.register_event_handler("klippy:disconnect",self.handle_disconnect)
		#
		#register TFT_NOTIFY gcode command
		#
		gcode = self.printer.lookup_object('gcode')
		gcode.register_command('TFT_NOTIFY', self.cmd_TFT_NOTIFY,
		                       desc='Send a notification pop-up to the TFT35')

	#
	#open serial port to device
	#
	def openDevice(self,device,baud,timeout):
		if timeout==0:
			serialPort=serial.Serial(device,baud)
		else:
			serialPort=serial.Serial(device,baud,timeout=timeout)
		return serialPort

	#
	#event handler when printer is ready
	#
	def handle_ready(self):
		#
		#create connections to devices if needed
		#
		_log.debug('handle_ready called')
		if self.tftSerial==None:
			try:
				self.tftSerial=self.openDevice(self.tftDevice,self.tftBaud,self.tftTimeout)
				_log.debug('tftSerial opened: %s',self.tftDevice)
			except Exception as e:
				_log.exception('tftSerial open failed')
				self.tftSerial=None

		if self.klipperSerial==None:
			try:
				self.klipperSerial=self.openDevice(self.klipperDevice,self.klipperBaud,self.klipperTimeout)
				_log.debug('klipperSerial opened: %s',self.klipperDevice)
			except Exception as e:
				_log.exception('klipperSerial open failed')
				self.klipperSerial=None
		#
		#create and start threads
		#
		_log.debug('starting threads, tftSerial=%s klipperSerial=%s',self.tftSerial,self.klipperSerial)
		self.stopEvent.clear()
		threading.Thread(target=self.tft2klipper).start()
		threading.Thread(target=self.klipper2tft).start()
		#
		#register reactor timer for print state monitoring
		#
		reactor = self.printer.get_reactor()
		reactor.register_timer(self._monitor_callback, reactor.monotonic() + 2.0)

	#
	#forward data from TFT35 to Klipper
	#
	def tft2klipper(self):
		while True:
			#
			#if stopping thread event is set
			#
			if self.stopEvent.is_set():
				if self.tftSerial!=None:
					self.tftSerial.close()		#close connection to TFT35
				self.tftSerial=None			#clear property
				break
			#
			#otherwise read from TFT35 and forward to Klipper
			#
			tftSer = self.tftSerial
			klipSer = self.klipperSerial
			if tftSer!=None and klipSer!=None:
				try:
					line=tftSer.readline()
					if line!=b'':			#if readline timeout, it returns empty bytes
						_log.debug('FROM_TFT: %r',line)
						klipSer.write(line)
						if b'M108' in line:
							tftSer.write(b'//action:prompt_end\n')
							_log.debug('INJECTED: prompt_end')
				except:
					pass

	#
	#forward data from Klipper to TFT35
	#
	def klipper2tft(self):
		_log.debug('klipper2tft thread started')
		while True:
			#
			#if stopping thread event is set
			#
			if self.stopEvent.is_set():
				if self.klipperSerial!=None:
					self.klipperSerial.close()		#close connection to Klipper
				self.klipperSerial=None			#clear property
				break
			#
			#otherwise read from Klipper and forward to TFT35
			#
			tftSer = self.tftSerial
			klipSer = self.klipperSerial
			if tftSer!=None and klipSer!=None:
				try:
					line=klipSer.readline()
					if isinstance(line,bytes):
						line=line.decode('utf-8',errors='replace')
					if line:
						_log.debug('FROM_KLIPPER: %r',line)
						if line.startswith('!! '):
							msg=line[3:].strip()
							if 'pending gcode' in msg.lower():
								continue
							elif 'must home' in msg.lower():
								line=('//action:prompt_begin Must home axis first\n'
								      '//action:prompt_text '+msg+'\n'
								      '//action:prompt_button Dismiss|M108\n'
								      '//action:prompt_show\n')
							else:
								line='//action:notification '+msg+'\n'
						elif line.strip()=='echo: ok':
							continue
						elif line.startswith('// '):
							line=line[3:]
						#proactive temp reports lack 'ok' -- add it so the TFT
						#command queue stays unblocked in remote host print mode
						if not line.startswith('ok ') and _TEMP_RE.match(line):
							line='ok '+line
						_log.debug('TO_TFT: %r',line)
						tftSer.write(line.encode('utf-8'))
				except Exception as e:
					_log.exception('klipper2tft error')

	#
	#write a line directly to the TFT serial port (thread-safe via GIL for single write)
	#
	def _tft_write(self,line):
		if self.tftSerial is not None:
			try:
				self.tftSerial.write(line.encode('utf-8'))
				_log.debug('MONITOR_TO_TFT: %r',line)
			except Exception:
				pass

	#
	#gcode command: TFT_NOTIFY MSG="text" -- sends notification directly to TFT serial
	#
	def cmd_TFT_NOTIFY(self,gcmd):
		msg = gcmd.get('MSG','')
		self._tft_write('//action:notification '+msg+'\n')

	#
	#reactor timer callback -- runs in Klipper main thread, safe to call lookup_object
	#
	def _monitor_callback(self,eventtime):
		#stop rescheduling if disconnect signalled
		if self.stopEvent.is_set():
			return self.printer.get_reactor().NEVER
		try:
			ps = self.printer.lookup_object('print_stats').get_status(eventtime)
			vsd = self.printer.lookup_object('virtual_sdcard').get_status(eventtime)
			state = ps.get('state','standby')
			last  = self.last_print_state

			#lifecycle transitions
			if state == 'printing' and last != 'printing':
				self._tft_write('//action:print_start\n')
				self.last_print_state = 'printing'
			elif state == 'paused' and last == 'printing':
				self._tft_write('//action:pause\n')
				self.last_print_state = 'paused'
			elif state == 'printing' and last == 'paused':
				self._tft_write('//action:resume\n')
				self.last_print_state = 'printing'
			elif state in ('complete','cancelled') and last != 'standby':
				action = 'cancel' if state == 'cancelled' else 'print_end'
				self._tft_write('//action:'+action+'\n')
				self.last_print_state = 'standby'
			elif state == 'standby' and last in ('printing','paused'):
				self._tft_write('//action:print_end\n')
				self.last_print_state = 'standby'
			elif state == 'standby' and last != 'standby':
				self.last_print_state = 'standby'

			#progress updates while active
			if state in ('printing','paused'):
				file_size = int(vsd.get('file_size', 0))
				file_pos  = int(vsd.get('file_position', 0))
				if file_size > 0:
					self._tft_write('//action:notification Data Left %d/%d\n' % (file_pos, file_size))

				duration = float(ps.get('print_duration', 0))
				progress = float(vsd.get('progress', 0))
				if progress > 0.001 and duration > 0:
					remaining = int((duration / progress) - duration)
					rh = remaining // 3600
					rm = (remaining % 3600) // 60
					rs = remaining % 60
					self._tft_write('//action:notification Time Left %dh%dm%ds\n' % (rh, rm, rs))

				info      = ps.get('info', {})
				cur_layer   = int(info.get('current_layer', 0) or 0)
				total_layer = int(info.get('total_layer', 0) or 0)
				if total_layer > 0:
					z_pos = self.printer.lookup_object('toolhead').get_position()[2]
					self._tft_write('//action:notification Layer Left %d/%d Z%.2fmm\n' % (cur_layer, total_layer, z_pos))

		except Exception:
			_log.exception('_monitor_callback error')

		interval = 3.0 if self.last_print_state in ('printing','paused') else 5.0
		return eventtime + interval

	#
	#event handler when printer is disconnected
	#
	def handle_disconnect(self):
		self.stopEvent.set()	#signal threads to stop

#
#config loading function of add-on
#
def load_config(config):
	return TftBridge(config)
