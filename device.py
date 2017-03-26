"""
This module represents a device.

Computer Systems Architecture Course
Assignment 1
March 2016
"""
import Queue
from threading import Event, Thread, Lock, Semaphore
from barrier import ReusableBarrierCond


class Device(object):
    """
    Class that represents a device.
    """

    def __init__(self, device_id, sensor_data, supervisor):
        """
        Constructor.

        @type device_id: Integer
        @param device_id: the unique id of this node; between 0 and N-1

        @type sensor_data: List of (Integer, Float)
        @param sensor_data: a list containing (location, data) as measured by this device

        @type supervisor: Supervisor
        @param supervisor: the testing infrastructure's control and validation component
        """
        self.device_id = device_id
        self.sensor_data = sensor_data
        self.supervisor = supervisor
        self.scripts = []
        self.timepoint_done = Event()

        # list of devices
        self.devices = []

        # event to wait for setup to finish
        self.event_setup = Event()

        # a barrier for devices
        self.barrier_device = None

        # a location lock
        self.locations_lock = []

        # a lock for changing data
        self.data_set_lock = Lock()

        self.thread = DeviceThread(self)
        self.thread.start()

        # order to shutdown the working threads
        self.device_shutdown_order = False

        # work que for scripts
        self.work_queue = Queue.Queue()

        # barrier for workers
        self.worker_barrier = ReusableBarrierCond(8)

        # semaphore for data when is added to the working queue
        self.data_semaphore = Semaphore(value=0)

        # semafor for checking the number of finished scripts
        self.worker_semaphore = Semaphore(value=0)

    def __str__(self):
        """
        Pretty prints this device.

        @rtype: String
        @return: a string containing the id of this device
        """
        return "Device %d" % self.device_id

    def setup_devices(self, devices):
        """
        Setup the devices before simulation begins.

        @type devices: List of Device
        @param devices: list containing all devices
        """
        if self.device_id == 0:
            self.barrier_device = ReusableBarrierCond(len(devices))

            for _ in range(25):
                self.locations_lock.append(Lock())

            for dev in devices:
                dev.devices = devices
                dev.barrier_device = self.barrier_device
                dev.locations_lock = self.locations_lock
                dev.event_setup.set()

    def assign_script(self, script, location):
        """
        Provide a script for the device to execute.

        @type script: Script
        @param script: the script to execute from now on at each timepoint; None if the
            current timepoint has ended

        @type location: Integer
        @param location: the location for which the script is interested in
        """
        if script is not None:
            self.scripts.append((script, location))
        else:
            self.timepoint_done.set()

    def get_data(self, location):
        """
        Returns the pollution value this device has for the given location.

        @type location: Integer
        @param location: a location for which obtain the data

        @rtype: Float
        @return: the pollution value
        """
        return self.sensor_data[location] if location in self.sensor_data else None

    def set_data(self, location, data):
        """
        Sets the pollution value stored by this device for the given location.

        @type location: Integer
        @param location: a location for which to set the data

        @type data: Float
        @param data: the pollution value
        """
        with self.data_set_lock:
            if location in self.sensor_data:
                self.sensor_data[location] = data

    def shutdown(self):
        """
        Instructs the device to shutdown (terminate all threads). This method
        is invoked by the tester. This method must block until all the threads
        started by this device terminate.
        """
        self.thread.join()


class DeviceThread(Thread):
    """
    Class that implements the device's worker thread.
    """

    def __init__(self, device):
        """
        Constructor.

        @type device: Device
        @param device: the device which owns this thread
        """
        Thread.__init__(self, name="Device Thread %d" % device.device_id)
        self.device = device

    def run(self):

    	# make devices wait for setup
        self.device.event_setup.wait()

        # create worker threads
        list_threads = []
        for i in range(8):
            thrd = WorkerThread(self.device, self.device.locations_lock, self.device.work_queue, i)
            list_threads.append(thrd)

        for thrd in list_threads:
            thrd.start()

        # number of scripts added (will be used to check if all are done)
        script_number = 0

        while True:
            # get the current neighbourhood
            neighbours = self.device.supervisor.get_neighbours()
            if neighbours is None:
                break

            # wait for end of timepoint
            self.device.timepoint_done.wait()

            # run scripts received until now
            for (script, location) in self.device.scripts:
                tup = (script, location, neighbours)

                # put the script in the work queue
                self.device.work_queue.put(tup)

                # increment the number of scripts added
                self.device.data_semaphore.release()

                script_number += 1


            self.device.timepoint_done.clear()

            # wait for all devices to get here
            self.device.barrier_device.wait()

        # wait for all scripts to be finished
        for _ in xrange(script_number):
            self.device.worker_semaphore.acquire()

        # tell worker threads to shutdown
        self.device.device_shutdown_order = True

        # release them so they can finish
        for _ in xrange(8):
            self.device.data_semaphore.release()

        # join threads
        for thrd in list_threads:
            thrd.join()


class WorkerThread(Thread):
    """
    Class that represents a worker thread.
    """

    def __init__(self, device, locations_lock, work_queue, worker_id):
        """
        Constructor.

        @type device: Device
        @param device_id: the parent of the device thread

        @type locations_lock: List
        @param locations_lock: a list containing locks for all locations

        @type work_queue: Queue
        @param work_queue: queue of tuples containing the script,location and neighbours

		@type worker_id: Integer
        @param worker_id: the worker id
        """

        Thread.__init__(self, name="Worker Thread %d" % worker_id)
        self.device = device
        self.locations_lock = locations_lock
        self.work_queue = work_queue
        self.worker_id = worker_id


    def run(self):

        while  True:
        	# wait for data to be added to the work que
            self.device.data_semaphore.acquire()

            # if the shutdown order is given the break
            if self.device.device_shutdown_order is True:
                break

            # get tuple and extract info
            tup = self.work_queue.get()
            script = tup[0]
            location = tup[1]
            neighbours = tup[2]

            # using the location lock
            with self.locations_lock[location]:
                script_data = []
                # collect data from current neighbours
                for device in neighbours:
                    data = device.get_data(location)
                    if data is not None:
                        script_data.append(data)
                # add our data, if any
                data = self.device.get_data(location)
                if data is not None:
                    script_data.append(data)

                if script_data != []:
                    # run script on data
                    result = script.run(script_data)

                    # update data of neighbours
                    for device in neighbours:
                        device.set_data(location, result)
                    # update our data
                    self.device.set_data(location, result)
            # decrement the counter for the finished scripts
            self.device.worker_semaphore.release()

        # wait for all worker threads to reach this point
        self.device.worker_barrier.wait()
