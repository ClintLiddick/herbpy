PACKAGE = 'herbpy'
import logging, prpy
import openravepy

logger = logging.getLogger('herbpy')

class HERBRobot(prpy.base.WAMRobot):
    def __init__(self, left_arm_sim, right_arm_sim, right_ft_sim,
                       left_hand_sim, right_hand_sim, left_ft_sim,
                       head_sim, vision_sim, talker_sim, segway_sim):
        from openravepy import RaveCreateController, RaveCreateSensorSystem

        prpy.base.WAMRobot.__init__(self, robot_name='herb')

        env = self.GetEnv()

        # Absolute path to this package.
        from rospkg import RosPack
        ros_pack = RosPack()
        package_path = ros_pack.get_path(PACKAGE)

        # Convenience attributes for accessing self components.
        self.left_arm = self.GetManipulator('left')
        self.right_arm = self.GetManipulator('right')
        self.head = self.GetManipulator('head')
        self.left_arm.hand = self.left_arm.GetEndEffector()
        self.right_arm.hand = self.right_arm.GetEndEffector()
        self.left_hand = self.left_arm.hand
        self.right_hand = self.right_arm.hand
        self.manipulators = [ self.left_arm, self.right_arm, self.head ]

        # Dynamically switch to self-specific subclasses.
        from herbbase import HerbBase
        from prpy.base import BarrettHand, WAM
        from herbpantilt import HERBPantilt
        prpy.bind_subclass(self.left_arm, WAM, sim=left_arm_sim,
                           owd_namespace='/left/owd')
        prpy.bind_subclass(self.right_arm, WAM, sim=right_arm_sim,
                           owd_namespace='/right/owd')
        prpy.bind_subclass(self.head, HERBPantilt, sim=head_sim,
                           owd_namespace='/head/owd')
        prpy.bind_subclass(self.left_arm.hand, BarrettHand, sim=left_hand_sim,
                           manipulator=self.left_arm, ft_sim=right_ft_sim,
                           owd_namespace='/left/owd', bhd_namespace='/left/bhd')
        prpy.bind_subclass(self.right_arm.hand, BarrettHand, sim=right_hand_sim,
                           manipulator=self.right_arm, ft_sim=right_ft_sim,
                           owd_namespace='/right/owd', bhd_namespace='/right/bhd')
        self.base = HerbBase(sim=segway_sim, robot=self)
        
        # Support for named configurations.
        import os.path
        self.configurations.add_group('left_arm', self.left_arm.GetArmIndices())
        self.configurations.add_group('right_arm', self.right_arm.GetArmIndices())
        self.configurations.add_group('head', self.head.GetArmIndices())
        self.configurations.add_group('left_hand', self.left_hand.GetIndices())
        self.configurations.add_group('right_hand', self.right_hand.GetIndices())

        if prpy.dependency_manager.is_catkin():
            from catkin.find_in_workspaces import find_in_workspaces
            configurations_paths = find_in_workspaces(search_dirs=['share'],
                    project='herbpy', path='config/configurations.yaml',
                    first_match_only=True)

            if not configurations_paths:
                raise ValueError('Unable to load named configurations from'
                                 ' "config/configurations.yaml".')

            configurations_path = configurations_paths[0]
        else:
            configurations_path = os.path.join(package_path,
                    'config/configurations.yaml')

        try:
            self.configurations.load_yaml(configurations_path)
        except IOError as e:
            raise ValueError(
                'Failed loading named configurations from "{:s}".'.format(
                    configurations_path))

        # Load default TSRs from YAML.
        if self.tsrlibrary is not None:
            if prpy.dependency_manager.is_catkin():
                from catkin.find_in_workspaces import find_in_workspaces
                tsrs_paths = find_in_workspaces(search_dirs=['share'],
                        project='herbpy', path='config/tsrs.yaml',
                        first_match_only=True)

                if not tsrs_paths:
                    raise ValueError('Unable to load named tsrs from'
                                     ' "config/tsrs.yaml".')

                tsrs_path = tsrs_paths[0]
            else:
                tsrs_path = os.path.join(package_path, 'config/tsrs.yaml')

            try:
                self.tsrlibrary.load_yaml(tsrs_path)
            except IOError as e:
                raise ValueError('Failed loading named tsrs from "{:s}".'.format(
                    tsrs_path))

        # Initialize a default planning pipeline.
        from prpy.planning import Planner, Sequence, Ranked
        from prpy.planning import (CBiRRTPlanner, CHOMPPlanner, IKPlanner,
                                   MKPlanner, NamedPlanner, SnapPlanner,
                                   SBPLPlanner, OMPLPlanner)

        self.cbirrt_planner = CBiRRTPlanner()
        self.mk_planner = MKPlanner()
        self.snap_planner = SnapPlanner()
        self.named_planner = NamedPlanner()
        self.ik_planner = IKPlanner()
        self.chomp_planner = CHOMPPlanner()
        self.ompl_planner = OMPLPlanner(algorithm='RRTConnect')
        self.planner = Sequence(
            self.ik_planner,
            self.named_planner,
            self.snap_planner, 
            self.mk_planner,
            self.chomp_planner,
            self.ompl_planner,
            self.cbirrt_planner
        )

        # Base planning
        if prpy.dependency_manager.is_catkin():
            from catkin.find_in_workspaces import find_in_workspaces
            planner_parameters_paths = find_in_workspaces(search_dirs=['share'],
                project='herbpy', path='config/base_planner_parameters.yaml',
                first_match_only=True)

            if not planner_parameters_paths:
                raise ValueError('Unable to load base planner parameters from'
                                 ' "config/base_planner_parameters.yaml".')

            planner_parameters_path = planner_parameters_paths[0]
        else:
            planner_parameters_path = os.path.join(
                    package_path, 'config/base_planner_parameters.yaml')

        self.sbpl_planner = SBPLPlanner()
        try:
            with open(planner_parameters_path, 'rb') as config_file:
                import yaml
                params_yaml = yaml.load(config_file)
            self.sbpl_planner.SetPlannerParameters(params_yaml)
        except IOError as e:
            raise ValueError(
                'Failed loading base planner parameters from "{:s}".'.format(
                    planner_parameters_path))

        self.base_planner = self.sbpl_planner

        # Setting necessary sim flags
        self.talker_simulated = talker_sim
        self.segway_sim = segway_sim
        self.vision_sim = vision_sim

        if not self.vision_sim:
            args = 'MarkerSensorSystem {0:s} {1:s} {2:s} {3:s} {4:s}'.format(
                        'herbpy', '/herbpy', '/head/wam2', 'herb', '/head/wam2')

            self.vision_sensorsystem = RaveCreateSensorSystem(env, args)
            if self.vision_sensorsystem is None:
                raise Exception("creating the marker vision sensorsystem failed")

        # Load a standalone ROSController. We don't attach this controller for
        # the robot and, instead, only use it as a library to publish standard
        # ROS trajectory_msgs/JointTrajectory messages. In the future, we may
        # switch to using this controller for all of HERB.
        # TODO: Publish HandClose and HandOpen as trajectories.
        # TODO: Publish the Segway's motion as a trajectory (i.e. affine DOFs).
        if not (left_arm_sim and right_arm_sim and head_sim):
            logger.debug('Loading ROSController plugin for trajectory visualization.')

            # Args: controller_type node_name namespace [extra_flags [...]]
            args = 'ROSController prpy visualization noread'
            self.ros_controller = RaveCreateController(env, args)

            dof_indices  = []
            dof_indices.extend(self.head.GetArmIndices())
            dof_indices.extend(self.left_arm.GetArmIndices())
            dof_indices.extend(self.right_arm.GetArmIndices())

            if self.ros_controller is not None:
                logger.debug('Initializing ROSController with DOFs %s.', dof_indices)
                self.ros_controller.Init(self, dof_indices, 0)
            else:
                logger.warning('Unable to load ROSController plugin. Trajectory'
                               ' visualization messages will not be published.'
                               ' Do you have or_ros_control installed?')
        else:
            logger.debug('Skipping laoding ROSController plugin because'
                         ' left_arm_sim, right_arm_sim, and head_sim are True.')


    def CloneBindings(self, parent):
        from prpy import Cloned
        prpy.base.WAMRobot.CloneBindings(self, parent)
        self.left_arm = Cloned(parent.left_arm)
        self.right_arm = Cloned(parent.right_arm)
        self.head = Cloned(parent.head)
        self.left_arm.hand = Cloned(parent.left_arm.GetEndEffector())
        self.right_arm.hand = Cloned(parent.right_arm.GetEndEffector())
        self.left_hand = self.left_arm.hand
        self.right_hand = self.right_arm.hand
        self.manipulators = [ self.left_arm, self.right_arm, self.head ]
        self.planner = parent.planner
        self.base_planner = parent.base_planner

    def Say(robot, message):
        """Say a message using HERB's text-to-speech engine.
        @param message
        """
        from pr_msgs.srv import AppletCommand
        import rospy

        if not robot.talker_simulated:
            # XXX: HerbPy should not make direct service calls.
            logger.info('Saying "%s".', message)
            #rospy.wait_for_service('/talkerapplet')
            talk = rospy.ServiceProxy('/talkerapplet', AppletCommand)    
            try:
                talk('say', message, 0, 0)
            except rospy.ServiceException, e:
                logger.error('Error talking.')

    def SetStiffness(self, stiffness):
        """Set the stiffness of HERB's arms and head.
        Zero is gravity compensation, one is position control. Stifness values
        between zero and one are experimental.
        @param stiffness value between zero and one
        """
        self.head.SetStiffness(stiffness)
        self.left_arm.SetStiffness(stiffness)
        self.right_arm.SetStiffness(stiffness)

    def WaitForObject(robot, obj_name, timeout=None, update_period=0.1):
        """Wait for the perception system to detect an object.
        This function will block until either: (1) an object of the appropriate
        type has been detected by the perception system or (2) a timeout
        occurs. The name of an object is generally equal to its filename
        without the ".kinbody.xml" extension; e.g. the name of
        fuze_bottle.kinbody.xml is fuze_bottle.
        @param obj_name type of object to wait for
        @param timeout maximum time to wait in seconds or None to wait forever
        @param update_period period at which to poll the sensor
        @return perceived KinBody or None if a timeout occured
        """
        import time

        start = time.time()
        found_body = None

        if not robot.vision_sim:
            robot.vision_sensorsystem.SendCommand('enable')
        else:
            # Timeout immediately in simulation.
            timeout = 0

        logger.info("Waiting for object %s to appear.", obj_name)
        try:
            while True:
                # Check for an object with the appropriate name.
                bodies = robot.GetEnv().GetBodies()
                for body in bodies:
                    if body.GetName().startswith('vision_' + obj_name):
                        return body

                # Check for a timeout.
                if timeout is not None and time.time() - start >= timeout:
                    logger.info("Timed out without finding object.")
                    return None

                time.sleep(update_period)
        finally:
            if not robot.vision_sim:
                robot.vision_sensorsystem.SendCommand('Disable')

    def DriveStraightUntilForce(robot, direction, velocity=0.1,
                                force_threshold=3.0, max_distance=None,
                                timeout=None, left_arm=True, right_arm=True):
        """Deprecated. Use base.DriveStraightUntilForce instead.
        """
        logger.warning('DriveStraightUntilForce is deprecated. Use'
                       ' base.DriveStraightUntilForce instead.')
        robot.base.DriveStraightUntilForce(direction, velocity, force_threshold,
                                max_distance, timeout, left_arm, right_arm)

    def DriveAlongVector(robot, direction, goal_pos):
        import numpy
        # TODO: Do we still need this? If so, we should move it into HERBBase.
        direction = direction[:2]/numpy.linalg.norm(direction[:2])
        herb_pose = robot.GetTransform()
        distance = numpy.dot(goal_pos[:2]-herb_pose[:2,3], direction)
        cur_angle = numpy.arctan2(herb_pose[1,0],herb_pose[0,0])
        des_angle = numpy.arctan2(direction[1],direction[0])
        robot.RotateSegway(des_angle-cur_angle)
        robot.DriveSegway(distance)

    def DriveSegway(robot, meters, **kw_args):
        """Deprecated. Use base.Forward instead.
        """
        logger.warning('DriveSegway is deprecated. Use base.Forward instead.')
        robot.base.Forward(meters, **kw_args)

    def DriveSegwayToNamedPosition(robot, named_position):
        """Deprecated. Use base.PlanToBasePose instead.
        """
        if robot.segway_sim:
            raise Exception('Driving to named positions is not supported'
                            ' in simulation.')
        else:
            robot.base.controller.SendCommand("Goto " + named_position)

    def RotateSegway(robot, angle_rad, **kw_args):
        """Deprecated. Use base.Rotate instead.
        """
        logger.warning('RotateSegway is deprecated. Use base.Rotate instead.')
        robot.base.Rotate(angle_rad, **kw_args)

    def StopSegway(robot):
        # TODO: This should be moved into HERBBase.
        if not robot.segway_sim:
            robot.base.controller.SendCommand("Stop")
