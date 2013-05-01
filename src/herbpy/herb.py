import logging, numpy, openravepy, rospy, time
import herbpy, exceptions, planner, util
from util import Deprecated

logger = logging.getLogger('herbpy')

class Herb(openravepy.Robot):
    def Say(robot, message):
        """
        Say a message using HERB's text-to-speech engine.
        @param message
        """
        from pr_msgs.srv import AppletCommand

        if not robot.talker_simulated:
            # XXX: HerbPy should not make direct service calls.
            logger.info('Saying "%s".', message)
            rospy.wait_for_service('/talkerapplet')
            talk = rospy.ServiceProxy('/talkerapplet', AppletCommand)    
            try:
                talk('say', message, 0, 0)
            except rospy.ServiceException, e:
                logger.error('Error talking.')

    def PlanGeneric(robot, command_name, *args, **kw_args):
        traj = None
        with robot.GetEnv():
            # Update the controllers to get new joint values.
            robot.GetController().SimulationStep(0)

            # Sequentially try each planner until one succeeds.
            with robot:
                for delegate_planner in robot.planners:
                    with util.Timer('Planning with %s' % delegate_planner.GetName()):
                        try:
                            traj = getattr(delegate_planner, command_name)(*args, **kw_args)
                            break
                        except planner.UnsupportedPlanningError, e:
                            logger.debug('Unable to plan with {0:s}: {1:s}'.format(delegate_planner.GetName(), e))
                        except planner.PlanningError, e:
                            logger.warning('Planning with {0:s} failed: {1:s}'.format(delegate_planner.GetName(), e))
                            # TODO: Log the scene and planner parameters to a file.

        if traj is None:
            raise planner.PlanningError('Planning failed with all planners.')
        else:
            logger.info('Planning succeeded with %s.', delegate_planner.GetName())

        # Strip all inactive DOFs from the trajectory.
        config_spec = robot.GetActiveConfigurationSpecification()
        openravepy.planningutils.ConvertTrajectorySpecification(traj, config_spec)

        # Optionally execute the trajectory.
        if 'execute' not in kw_args or kw_args['execute']:
            return robot.ExecuteTrajectory(traj, **kw_args)
        else:
            return traj

    def PlanToNamedConfiguration(robot, name, execute=True, **kw_args):
        config_inds = numpy.array(robot.configs[name]['dofs'])
        config_vals = numpy.array(robot.configs[name]['vals'])

        with robot:
            robot.SetActiveDOFs(config_inds)
            traj = robot.PlanToConfiguration(config_vals, execute=False, **kw_args)

        if execute:
            return robot.ExecuteTrajectory(traj, **kw_args)
        else:
            return traj

    def AddNamedConfiguration(robot, name, dofs, vals):
        if len(dofs) != len(vals):
            raise Exception('AddNamedConfiguration Failed. Lengths of dofs and vals must be equal:\n\tconfig_inds=%s\n\tconfig_vals=[%s]'%(str(config_indxs), SerializeArray(config_vals)))

        robot.configs[name] = {}
        robot.configs[name]['dofs'] = numpy.array( dofs )
        robot.configs[name]['vals'] = numpy.array( vals )

    def RetimeTrajectory(robot, traj, max_jerk=30.0, synchronize=False,
                         stop_on_stall=True, stop_on_ft=False, force_direction=None,
                         force_magnitude=None, torque=None, **kw_args):
        """
        Retime a generic OpenRAVE trajectory into a timed MacTrajectory.
        @param traj input trajectory
        @param max_jerk maximum jerk allowed during retiming
        @return timed MacTrajectory
        """
        # Fall back on the standard OpenRAVE retimer if MacTrajectory is not
        # available.
        if robot.mac_retimer is None:
            logger.warning('MacTrajectory is not available. Falling back to RetimeTrajectory.')
            openravepy.planningutils.RetimeTrajectory(traj)
            return traj

        # Create a MacTrajectory with timestamps, joint values, velocities,
        # accelerations, and blend radii.
        generic_config_spec = traj.GetConfigurationSpecification()
        generic_angle_group = generic_config_spec.GetGroupFromName('joint_values')
        path_config_spec = openravepy.ConfigurationSpecification()
        path_config_spec.AddDeltaTimeGroup()
        path_config_spec.AddGroup(generic_angle_group.name, generic_angle_group.dof, '')
        path_config_spec.AddDerivativeGroups(1, False);
        path_config_spec.AddDerivativeGroups(2, False);
        path_config_spec.AddGroup('owd_blend_radius', 1, 'next')
        path_config_spec.ResetGroupOffsets()

        # Initialize the MacTrajectory.
        mac_traj = openravepy.RaveCreateTrajectory(robot.GetEnv(), 'MacTrajectory')
        mac_traj.Init(path_config_spec)

        # Copy the joint values and blend radii into the MacTrajectory.
        num_waypoints = traj.GetNumWaypoints()
        for i in xrange(num_waypoints):
            waypoint = traj.GetWaypoint(i, path_config_spec)
            mac_traj.Insert(i, waypoint, path_config_spec)

        # Serialize the planner parameters.
        params = [ 'max_jerk', str(max_jerk) ]
        if stop_on_stall:
            params += [ 'cancel_on_stall' ]
        if stop_on_ft:
            force_threshold = force_magnitude * numpy.array(force_direction)
            params += [ 'cancel_on_ft' ]
            params += [ 'force_threshold' ] + map(str, force_threshold)
            params += [ 'torque_threshold' ] + map(str, torque)
        if synchronize:
            params += [ 'synchronize' ]

        # Retime the newly-created MacTrajectory.
        params_str = ' '.join(params)
        logger.info('Created MacTrajectory with flags: %s', params_str)
        retimer_params = openravepy.Planner.PlannerParameters()
        retimer_params.SetExtraParameters(params_str)
        robot.mac_retimer.InitPlan(robot, retimer_params)
        robot.mac_retimer.PlanPath(mac_traj)
        return mac_traj

    def BlendTrajectory(robot, traj, maxsmoothiter=None, resolution=None,
                        blend_radius=0.2, blend_attempts=4, blend_step_size=0.05,
                        linearity_threshold=0.1, ignore_collisions=None, **kw_args):
        """
        Blend a trajectory. This appends a blend_radius group to an existing
        trajectory.
        @param traj input trajectory
        @return blended_trajectory trajectory with additional blend_radius group
        """
        with robot.GetEnv():
            with robot.CreateRobotStateSaver():
                return robot.trajectory_module.blendtrajectory(traj=traj, execute=False,
                        maxsmoothiter=maxsmoothiter, resolution=resolution,
                        blend_radius=blend_radius, blend_attempts=blend_attempts,
                        blend_step_size=blend_step_size, linearity_threshold=linearity_threshold,
                        ignore_collisions=ignore_collisions)

    def AddTrajectoryFlags(robot, traj, stop_on_stall=True, stop_on_ft=False,
                           force_direction=None, force_magnitude=None, torque=None):
        """
        Add OWD trajectory execution options to a trajectory. These options are
        encoded in the or_owd_controller group. The force_direction, force_magnitude,
        and torque parameters must be specified if stop_on_ft is True.
        @param traj input trajectory
        @param stop_on_stall stop the trajectory if the stall torques are exceeded
        @param stop_on_ft stop the trajectory on force/torque sensor input
        @param force_direction unit vector of the expected force in the hand frame
        @param force_magnitude maximum force magnitude in meters
        @param torque maximum torque in the hand frame in Newton-meters
        @return annotated_traj trajectory annotated with OWD execution options
        """
        flags  = [ 'or_owd_controller' ]
        flags += [ 'stop_on_stall', str(int(stop_on_stall)) ]
        flags += [ 'stop_on_ft', str(int(stop_on_ft)) ]

        if stop_on_ft:
            if force_direction is None:
                logger.error('Force direction must be specified if stop_on_ft is true.')
                return None
            elif force_magnitude is None:
                logger.error('Force magnitude must be specified if stop_on_ft is true.')
                return None 
            elif torque is None:
                logger.error('Torque must be specified if stop_on_ft is true.')
                return None 
            elif len(force_direction) != 3:
                logger.error('Force direction must be a three-dimensional vector.')
                return None
            elif len(torque) != 3:
                logger.error('Torque must be a three-dimensional vector.')
                return None

            flags += [ 'force_direction' ] + [ str(x) for x in force_direction ]
            flags += [ 'force_magnitude', str(force_magnitude) ]
            flags += [ 'torque' ] + [ str(x) for x in torque ]

        # Add a bogus group to the trajectory to hold these parameters.
        flags_str = ' '.join(flags)
        config_spec = traj.GetConfigurationSpecification();
        group_offset = config_spec.AddGroup(flags_str, 1, 'next')

        annotated_traj = openravepy.RaveCreateTrajectory(robot.GetEnv(), '')
        annotated_traj.Init(config_spec)
        for i in xrange(traj.GetNumWaypoints()):
            waypoint = numpy.zeros(config_spec.GetDOF())
            waypoint[0:-1] = traj.GetWaypoint(i)
            annotated_traj.Insert(i, waypoint)

        return annotated_traj

    def ExecuteTrajectory(robot, traj, timeout=None, blend=True, retime=True, **kw_args):
        """
        Execute a trajectory. By default, this retimes, blends, and adds the
        stop_on_stall flag to all trajectories. Additionally, this function blocks
        until trajectory execution finishes. This can be changed by changing the
        timeout parameter to a maximum number of seconds. Pass a timeout of zero to
        return instantly.
        @param traj trajectory to execute
        @param timeout blocking execution timeout
        @param blend compute blend radii before execution
        @param retime retime the trajectory before execution
        @return executed_traj
        """
        # Query the active manipulators based on which DOF indices are
        # included in the trajectory.
        active_indices = util.GetTrajectoryIndices(traj)
        active_manipulators = util.GetTrajectoryManipulators(robot, traj)
        needs_synchronization = len(active_manipulators) > 1
        any_sim = robot.head_sim or robot.right_arm_sim or robot.left_arm_sim
        all_sim = robot.head_sim and robot.right_arm_sim and robot.left_arm_sim

        if needs_synchronization and any_sim and not all_sim:
            raise exceptions.SynchronizationException('Unable to execute synchronized trajectory with'
                          ' mixed simulated and real controllers. (head_sim=%d, right_arm_sim=%d, left_arm_sim=%d)' 
                          % (robot.head_sim, robot.right_arm_sim, robot.left_arm_sim))

        # Optionally blend and retime the trajectory before execution. Retiming
        # creates a MacTrajectory that can be directly executed by OWD.
        with robot:
            robot.SetActiveDOFs(active_indices)
            if blend:
                traj = robot.BlendTrajectory(traj)
            if retime:
                traj = robot.RetimeTrajectory(traj, synchronize=needs_synchronization, **kw_args)

        # Synchronization implicitly executes on all manipulators.
        if needs_synchronization:
            running_manipulators = set(robot.manipulators)
        else:
            running_manipulators = set(active_manipulators)

        # Reset old trajectory execution flags
        for manipulator in active_manipulators:
            manipulator.ClearTrajectoryStatus()

        robot.GetController().SetPath(traj)

        # Wait for trajectory execution to finish.
        running_controllers = [ manipulator.controller for manipulator in running_manipulators ]
        is_done = util.WaitForControllers(running_controllers, timeout=timeout)
            
        # Request the controller status from each manipulator.
        if is_done:
            with robot.GetEnv():
                for manipulator in active_manipulators:
                    status = manipulator.GetTrajectoryStatus()
                    if status == 'aborted':
                        raise exceptions.TrajectoryAborted('Trajectory aborted for %s' % manipulator.GetName())
                    elif status == 'stalled':
                        raise exceptions.TrajectoryStalled('Trajectory stalled for %s' % manipulator.GetName())

        return traj

    def WaitForObject(robot, obj_name, timeout=None, update_period=0.1):
        start = time.time()
        found_body = None

        if not robot.moped_sim:
            robot.moped_sensorsystem.SendCommand('Enable')
        else:
            # Timeout immediately in simulation.
            timeout = 0

        logger.info("Waiting for object %s to appear.", obj_name)
        try:
            while True:
                # Check for an object with the appropriate name in the environment.
                bodies = robot.GetEnv().GetBodies()
                for body in bodies:
                    if body.GetName().startswith('moped_' + obj_name):
                        return body

                # Check for a timeout.
                if timeout is not None and time.time() - start >= timeout:
                    logger.info("Timed out without finding object.")
                    return None

                time.sleep(update_period)
        finally:
            if not robot.moped_sim:
                robot.moped_sensorsystem.SendCommand('Disable')

    def DriveStraightUntilForce(robot, direction, velocity=0.1, force_threshold=3.0,
                                max_distance=None, timeout=None, left_arm=True, right_arm=True):
        """
        Drive the base in a direction until a force/torque sensor feels a force. The
        Segway first turns to face the desired direction, then drives forward at the
        specified velocity. The action terminates when max_distance is reached, the
        timeout is exceeded, or if a force is felt. The maximum distance and timeout
        can be disabled by setting the corresponding parameters to None.
        @param direction forward direction of motion in the world frame
        @param velocity desired forward velocity
        @param force_threshold threshold force in Newtons
        @param max_distance maximum distance in meters
        @param timeout maximum duration in seconds
        @param left_arm flag to use the left force/torque sensor
        @param right_arm flag to use the right force/torque sensor
        @return felt_force flag indicating whether the action felt a force
        """
        if robot.segway_sim:
            logger.warning('DriveStraightUntilForce does not work with a simulated Segway.')
            return

        if (robot.left_ft_sim and left_arm) or (robot.right_ft_sim and right_arm):
            raise Exception('DriveStraightUntilForce does not work with simulated force/torque sensors.')

        with util.Timer("Drive segway until force"):
            env = robot.GetEnv()
            direction = numpy.array(direction, dtype='float')
            direction /= numpy.linalg.norm(direction) 
            manipulators = list()
            if left_arm:
                manipulators.append(robot.left_arm)
            if right_arm:
                manipulators.append(robot.right_arm)

            if not manipulators:
                logger.warning('Executing DriveStraightUntilForce with no force/torque sensor for feedback.')

            # Rotate to face the right direction.
            with env:
                robot_pose = robot.GetTransform()
            robot_angle = numpy.arctan2(robot_pose[1, 0], robot_pose[0, 0])
            desired_angle = numpy.arctan2(direction[1], direction[0])
            robot.RotateSegway(desired_angle - robot_angle)


            # Soft-tare the force/torque sensors. Tare is too slow.
            initial_force = dict()
            for manipulator in manipulators:
                force, torque = manipulator.GetForceTorque()
                initial_force[manipulator] = force
            
            try:
                felt_force = False
                start_time = time.time()
                start_pos = robot_pose[0:3, 3]
                while True:
                    # Check if we felt a force on any of the force/torque sensors.
                    for manipulator in manipulators:
                        force, torque = manipulator.GetForceTorque()
                        if numpy.linalg.norm(initial_force[manipulator] - force) > force_threshold:
                            return True

                    # Check if we've exceeded the maximum distance.
                    with env:
                        current_pos = robot.GetTransform()[0:3, 3]
                    distance = numpy.dot(current_pos - start_pos, direction)
                    if max_distance is not None and distance >= max_distance:
                        return False

                    # Check for a timeout.
                    time_now = time.time()
                    if timeout is not None and time_now - star_time > timeout:
                        return False

                    # Continuously stream forward velocities.
                    robot.segway_controller.SendCommand('DriveInstantaneous {0:f} 0 0'.format(velocity))
            finally:
                # Stop the Segway before returning.
                robot.segway_controller.SendCommand('DriveInstantaneous 0 0 0')

    def DriveAlongVector(robot, direction, goal_pos):
        direction = direction[:2]/numpy.linalg.norm(direction[:2])
        herb_pose = robot.GetTransform()
        distance = numpy.dot(goal_pos[:2]-herb_pose[:2,3], direction)
        cur_angle = numpy.arctan2(herb_pose[1,0],herb_pose[0,0])
        des_angle = numpy.arctan2(direction[1],direction[0])
        robot.RotateSegway(des_angle-cur_angle)
        robot.DriveSegway(distance)

    def DriveSegway(robot, meters, timeout=None):
        with util.Timer("Drive segway"):
            if not robot.segway_sim:
                robot.segway_controller.SendCommand("Drive " + str(meters))
                if timeout == None:
                    robot.WaitForController(0)
                elif timeout > 0:
                    robot.WaitForController(timeout)
            # Create and execute base trajectory in simulation.
            else:
                with robot.GetEnv():
                    current_pose = robot.GetTransform().copy()
                    current_pose[0:3,3] = current_pose[0:3,3] + meters*current_pose[0:3,0]
                    robot.SetTransform(current_pose)

    def DriveSegwayToNamedPosition(robot, named_position):
        if robot.segway_sim:
            logger.warm('Drive to named positions not implemented in simulation.')
        else:
            robot.segway_controller.SendCommand("Goto " + named_position)

    def RotateSegway(robot, angle_rad, timeout=None):
        with util.Timer("Rotate segway"):
            if robot.segway_sim:
                with robot.GetEnv():
                    current_pose_in_world = robot.GetTransform().copy()
                    desired_pose_in_herb = numpy.array([[numpy.cos(angle_rad), -numpy.sin(angle_rad), 0, 0],
                                                        [numpy.sin(angle_rad), numpy.cos(angle_rad), 0, 0],
                                                        [0, 0, 1, 0],
                                                        [0, 0, 0, 1]])
                    desired_pose_in_world = numpy.dot(current_pose_in_world, desired_pose_in_herb)
                    robot.SetTransform(desired_pose_in_world)
            else:
                robot.segway_controller.SendCommand("Rotate " + str(angle_rad))
                if timeout == None:
                    robot.WaitForController(0)
                elif timeout > 0:
                    robot.WaitForController(timeout)

    def StopSegway(robot):
        if not robot.segway_sim:
            robot.segway_controller.SendCommand("Stop")

    @Deprecated('Use head.MoveHeadTo instead.')
    def MoveHeadTo(robot, target_values, execute=True, **kw_args):
        return robot.head.MoveHeadTo(target_values, execute, **kw_args)

    @Deprecated('Use head.FindIK instad.')
    def FindHeadDOFs(robot, target):
        return robot.head.FindIK(target)

    @Deprecated('Use head.LookAt instead.')
    def LookAt(robot, target, **kw_args):
        return robot.head.LookAt(target, **kw_args)
